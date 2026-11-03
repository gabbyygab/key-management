import io
import json
from io import StringIO
from unittest import mock
from urllib.error import HTTPError, URLError

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings

from accounts.models import User
from facilities.models import Building, Room

from .client import SupabaseClient, SupabaseError
from .conf import get_config
from .models import SyncCursor
from .registry import SPECS, select_specs
from .serializers import create_table_sql, foreign_key_sql, serialize, sync_fields
from .services import sync_all

CONFIGURED = override_settings(
    SUPABASE_URL="https://example.supabase.co", SUPABASE_SERVICE_ROLE_KEY="eyJservice-key",
    SUPABASE_SCHEMA="public", SUPABASE_TIMEOUT=5, SUPABASE_SYNC_ENABLED=True)


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def http_error(status, payload):
    return HTTPError("https://example.supabase.co", status, "err", {},
                     io.BytesIO(json.dumps(payload).encode()))


class FakeClient:
    """Records upserts instead of calling the network."""

    def __init__(self, fail_tables=()):
        self.sent = {}
        self.fail_tables = set(fail_tables)

    def upsert(self, table, rows, on_conflict="id"):
        if table in self.fail_tables:
            raise SupabaseError("boom", status=500)
        self.sent.setdefault(table, []).extend(rows)
        return len(rows)


class ConfigTests(TestCase):
    @override_settings(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="")
    def test_unconfigured_client_refuses_to_build(self):
        with self.assertRaisesMessage(SupabaseError, "SUPABASE_URL is not set"):
            SupabaseClient()

    @override_settings(SUPABASE_URL="file:///etc/passwd", SUPABASE_SERVICE_ROLE_KEY="k")
    def test_only_http_urls_are_accepted(self):
        self.assertTrue(get_config().problems())

    @override_settings(SUPABASE_URL="http://db.example.com", SUPABASE_SERVICE_ROLE_KEY="k")
    def test_plain_http_only_for_localhost(self):
        self.assertIn("https", " ".join(get_config().problems()))

    @override_settings(SUPABASE_URL="http://localhost:54321/", SUPABASE_SERVICE_ROLE_KEY="k")
    def test_local_supabase_is_allowed(self):
        self.assertEqual(get_config().problems(), [])
        self.assertEqual(get_config().url, "http://localhost:54321")


@CONFIGURED
class ClientTests(TestCase):
    @mock.patch("supabase_sync.client.urlopen")
    def test_upsert_request_shape(self, urlopen):
        urlopen.return_value = FakeResponse(b"")
        sent = SupabaseClient().upsert("keys_key", [{"id": 1}, {"id": 2}])
        self.assertEqual(sent, 2)
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.full_url,
                         "https://example.supabase.co/rest/v1/keys_key?on_conflict=id")
        self.assertEqual(req.get_header("Apikey"), "eyJservice-key")
        self.assertEqual(req.get_header("Authorization"), "Bearer eyJservice-key")
        self.assertIn("merge-duplicates", req.get_header("Prefer"))
        self.assertEqual(json.loads(req.data), [{"id": 1}, {"id": 2}])
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 5)

    @override_settings(SUPABASE_SERVICE_ROLE_KEY="sb_secret_abc123")
    @mock.patch("supabase_sync.client.urlopen")
    def test_new_style_secret_key_goes_in_apikey_only(self, urlopen):
        urlopen.return_value = FakeResponse(b"[]")
        SupabaseClient().select("keys_key")
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header("Apikey"), "sb_secret_abc123")
        self.assertIsNone(req.get_header("Authorization"))

    @mock.patch("supabase_sync.client.urlopen")
    def test_empty_upsert_makes_no_request(self, urlopen):
        self.assertEqual(SupabaseClient().upsert("keys_key", []), 0)
        urlopen.assert_not_called()

    @override_settings(SUPABASE_SCHEMA="keytrack")
    @mock.patch("supabase_sync.client.urlopen")
    def test_custom_schema_sets_profile_headers(self, urlopen):
        urlopen.return_value = FakeResponse(b"[]")
        SupabaseClient().select("keys_key")
        req = urlopen.call_args.args[0]
        self.assertEqual(req.get_header("Accept-profile"), "keytrack")

    @mock.patch("supabase_sync.client.time.sleep")
    @mock.patch("supabase_sync.client.urlopen")
    def test_retries_transient_errors_then_succeeds(self, urlopen, sleep):
        urlopen.side_effect = [http_error(503, {}), URLError("reset"), FakeResponse(b"[]")]
        self.assertEqual(SupabaseClient().select("keys_key"), [])
        self.assertEqual(urlopen.call_count, 3)

    @mock.patch("supabase_sync.client.time.sleep")
    @mock.patch("supabase_sync.client.urlopen")
    def test_client_errors_are_not_retried_and_parsed(self, urlopen, sleep):
        urlopen.side_effect = http_error(404, {"code": "PGRST205",
                                               "message": "Could not find the table"})
        with self.assertRaises(SupabaseError) as ctx:
            SupabaseClient().probe("keys_key")
        self.assertTrue(ctx.exception.table_missing)
        self.assertIn("Could not find the table", str(ctx.exception))
        self.assertEqual(urlopen.call_count, 1)


class SerializerTests(TestCase):
    def test_user_rows_never_include_the_password_hash(self):
        user = User.objects.create_user(username="u", email="u@example.com", password="x-Strong-123")
        spec = next(s for s in SPECS if s.model_label == "accounts.User")
        row = serialize(user, sync_fields(spec))
        self.assertNotIn("password", row)
        self.assertEqual(row["username"], "u")
        self.assertNotIn('"password"', create_table_sql(spec))

    def test_foreign_keys_and_timestamps_serialize_to_json(self):
        room = Room.objects.create(building=Building.objects.create(code="B", name="B"),
                                   room_code="1")
        spec = next(s for s in SPECS if s.model_label == "facilities.Room")
        row = serialize(room, sync_fields(spec))
        self.assertEqual(row["building_id"], room.building_id)
        json.dumps(row)  # must be JSON-safe
        self.assertIn("enable row level security", create_table_sql(spec))

    def test_existing_tables_gain_new_columns_when_the_schema_is_rerun(self):
        spec = next(s for s in SPECS if s.model_label == "scheduling.ScheduleBlock")
        sql = create_table_sql(spec)
        table = '"public"."scheduling_scheduleblock"'
        # Columns added after the table was first created must still arrive.
        self.assertIn(f'alter table {table} add column if not exists "section_id" bigint;', sql)
        self.assertIn(f'alter table {table} add column if not exists "offering_id" bigint;', sql)
        # Added columns stay nullable, and the primary key is never re-added.
        self.assertNotIn('add column if not exists "section_id" bigint not null', sql)
        self.assertNotIn('add column if not exists "id"', sql)

    def test_sections_and_offerings_are_mirrored_before_their_blocks(self):
        order = [s.model_label for s in SPECS]
        for parent in ("scheduling.Term", "scheduling.Subject", "scheduling.Section",
                       "accounts.Borrower", "facilities.Room"):
            self.assertLess(order.index(parent), order.index("scheduling.SubjectOffering"))
        self.assertLess(order.index("scheduling.Section"), order.index("scheduling.ScheduleBlock"))
        self.assertLess(order.index("scheduling.SubjectOffering"),
                        order.index("scheduling.ScheduleBlock"))

        offering = next(s for s in SPECS if s.model_label == "scheduling.SubjectOffering")
        fks = foreign_key_sql(offering, SPECS)
        # Term, subject and section are archivable now, so deleting one purges
        # its offerings; the nullable SET_NULL links (professor, preferred room)
        # detach instead of cascading.
        self.assertRegex(fks, r'"section_id"\) references "public"."scheduling_section" '
                              r'\("id"\)\s+on delete cascade')
        self.assertRegex(fks, r'"instructor_id"\) references "public"."accounts_borrower" '
                              r'\("id"\)\s+on delete set null')
        self.assertRegex(fks, r'"preferred_room_id"\) references "public"."facilities_room" '
                              r'\("id"\)\s+on delete set null')

        block = next(s for s in SPECS if s.model_label == "scheduling.ScheduleBlock")
        fks = foreign_key_sql(block, SPECS)
        self.assertRegex(fks, r'"offering_id"\) references "public"."scheduling_subjectoffering" '
                              r'\("id"\)\s+on delete set null')
        self.assertRegex(fks, r'"section_id"\) references "public"."scheduling_section" '
                              r'\("id"\)\s+on delete set null')

    def test_select_specs_rejects_unknown_tables(self):
        self.assertEqual([s.table_name for s in select_specs(["keys.Key"])], ["keys_key"])
        with self.assertRaises(LookupError):
            select_specs(["nope"])


@CONFIGURED
class SyncServiceTests(TestCase):
    def setUp(self):
        self.building = Building.objects.create(code="B", name="B")
        self.room = Room.objects.create(building=self.building, room_code="1")

    def test_incremental_sync_only_resends_changed_rows(self):
        client = FakeClient()
        sync_all(tables=["facilities_room"], client=client)
        self.assertEqual(len(client.sent["facilities_room"]), 1)
        cursor = SyncCursor.objects.get(table="facilities_room")
        self.assertEqual(cursor.last_status, SyncCursor.Status.OK)
        self.assertEqual(cursor.watermark, self.room.updated_at)

        Room.objects.create(building=self.building, room_code="2")
        client = FakeClient()
        sync_all(tables=["facilities_room"], client=client)
        # The boundary row is re-sent (>= watermark); upserts make that harmless.
        self.assertEqual(sorted(r["room_code"] for r in client.sent["facilities_room"]), ["1", "2"])

        client = FakeClient()
        sync_all(tables=["facilities_room"], client=client)
        self.assertEqual([r["room_code"] for r in client.sent["facilities_room"]], ["2"])

    def test_one_failing_table_does_not_stop_the_others(self):
        client = FakeClient(fail_tables={"facilities_building"})
        results = sync_all(tables=["facilities_building", "facilities_room"], client=client)
        self.assertEqual([r.ok for r in results], [False, True])
        cursor = SyncCursor.objects.get(table="facilities_building")
        self.assertEqual(cursor.last_status, SyncCursor.Status.ERROR)
        self.assertIn("boom", cursor.last_error)


class CommandTests(TestCase):
    @override_settings(SUPABASE_SYNC_ENABLED=False, SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="")
    @mock.patch("supabase_sync.client.urlopen")
    def test_disabled_is_a_quiet_noop(self, urlopen):
        out = StringIO()
        call_command("supabase_sync", stdout=out)
        self.assertIn("disabled", out.getvalue())
        urlopen.assert_not_called()

    @override_settings(SUPABASE_SYNC_ENABLED=True, SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="")
    def test_enabled_but_unconfigured_is_an_error(self):
        with self.assertRaisesMessage(CommandError, "SUPABASE_URL is not set"):
            call_command("supabase_sync", stdout=StringIO())

    @override_settings(SUPABASE_URL="", SUPABASE_SERVICE_ROLE_KEY="")
    def test_schema_and_dry_run_need_no_credentials(self):
        Building.objects.create(code="B", name="B")
        out = StringIO()
        call_command("supabase_sync", "--schema", stdout=out)
        self.assertIn('create table if not exists "public"."keys_key"', out.getvalue())
        out = StringIO()
        call_command("supabase_sync", "--dry-run", "--table", "facilities_building", stdout=out)
        self.assertIn("1 row(s)", out.getvalue())

    @CONFIGURED
    @mock.patch("supabase_sync.client.urlopen")
    def test_push_reports_rows(self, urlopen):
        urlopen.side_effect = lambda *a, **k: FakeResponse(b"")
        Building.objects.create(code="B", name="B")
        out = StringIO()
        call_command("supabase_sync", "--table", "facilities_building", stdout=out)
        self.assertIn("Pushed 1 row(s)", out.getvalue())

    @CONFIGURED
    @mock.patch("supabase_sync.client.urlopen")
    def test_check_reports_missing_tables(self, urlopen):
        urlopen.side_effect = http_error(404, {"code": "PGRST205", "message": "missing"})
        out = StringIO()
        with self.assertRaisesMessage(CommandError, "1 table(s) not ready"):
            call_command("supabase_sync", "--check", "--table", "keys_key", stdout=out)
        self.assertIn("missing", out.getvalue())
