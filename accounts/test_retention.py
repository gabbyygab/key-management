"""Archive retention: the clock, the cascade and the purge command.

What is being pinned down here:

* archiving stamps ``archived_at``, restoring clears it, and the stamp happens
  whichever path flipped the flag (archive button, bulk toolbar, ModelForm);
* a row is purged only once it has been archived longer than
  ARCHIVE_RETENTION_DAYS, and never while a key below it is out or a
  transaction below it is open;
* the purge cascades past PROTECT — a purged building takes its rooms, keys and
  borrow history with it — and writes an audit row that outlives the data;
* the Supabase mirror is asked to delete the purged parent only (its own
  cascading foreign keys do the rest), and the mirror is never touched in tests.
"""
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import retention
from accounts.models import Borrower, User
from audit.models import ActivityLog
from facilities.models import Building, GuardPost, Room
from keys.models import Key, KeyCategory
from transactions.models import BorrowTransaction, GuardShift


def make_world():
    """One building -> room -> key -> returned transaction, plus a guard."""
    admin = User.objects.create_user("ret-admin", email="ret-admin@x.test",
                                     password="x", role=User.Role.ADMIN)
    building = Building.objects.create(code="RET", name="Retention Hall")
    room = Room.objects.create(building=building, room_code="101")
    category = KeyCategory.objects.create(name="Retention category")
    key = Key.objects.create(key_code="RET-101-1", room=room, category=category)
    borrower = Borrower.objects.create(id_number="RET-1", first_name="Rita",
                                       last_name="Reyes",
                                       borrower_type=Borrower.BorrowerType.FACULTY)
    tx = BorrowTransaction.objects.create(
        transaction_no="RET-TX-1", key=key, borrower=borrower, room=room,
        released_by=admin, status=BorrowTransaction.Status.RETURNED,
        returned_at=timezone.now(), return_confirmed=True)
    return dict(admin=admin, building=building, room=room, category=category,
                key=key, borrower=borrower, tx=tx)


class ArchiveClockTests(TestCase):
    def setUp(self):
        self.w = make_world()
        self.client.force_login(self.w["admin"])

    def test_archive_button_stamps_the_clock_and_restore_clears_it(self):
        url = reverse("facilities:building_toggle", args=[self.w["building"].pk])
        self.client.post(url)
        building = Building.objects.get(pk=self.w["building"].pk)
        self.assertFalse(building.is_active)
        self.assertIsNotNone(building.archived_at)

        self.client.post(url)
        building.refresh_from_db()
        self.assertTrue(building.is_active)
        self.assertIsNone(building.archived_at)

    def test_a_form_that_unticks_is_active_also_stamps_the_clock(self):
        """BuildingForm/RoomForm/BorrowerForm expose is_active, so the clock
        cannot live in the archive views alone (ArchiveClockMixin)."""
        building = self.w["building"]
        building.is_active = False
        building.save()
        building.refresh_from_db()
        self.assertIsNotNone(building.archived_at)

    def test_key_uses_status_inactive_as_its_archived_state(self):
        key = self.w["key"]
        key.status = Key.Status.INACTIVE
        key.save()
        key.refresh_from_db()
        self.assertIsNotNone(key.archived_at)

        key.status = Key.Status.AVAILABLE
        key.save()
        key.refresh_from_db()
        self.assertIsNone(key.archived_at)

    def test_restoring_restarts_the_window_instead_of_resuming_it(self):
        borrower = self.w["borrower"]
        borrower.is_active = False
        borrower.save()
        Borrower.objects.filter(pk=borrower.pk).update(
            archived_at=timezone.now() - timedelta(days=90))

        borrower.refresh_from_db()
        borrower.is_active = True
        borrower.save()
        borrower.is_active = False
        borrower.save()
        borrower.refresh_from_db()
        self.assertGreater(borrower.archived_at,
                           timezone.now() - timedelta(minutes=1))


class DueQuerysetTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def archive(self, obj, *, days_ago):
        spec = retention.spec_for(obj)
        setattr(obj, spec.flag, spec.archived_value)
        obj.save()
        type(obj).objects.filter(pk=obj.pk).update(
            archived_at=timezone.now() - timedelta(days=days_ago))
        obj.refresh_from_db()
        return obj

    def test_nothing_is_due_before_the_window_closes(self):
        self.archive(self.w["borrower"], days_ago=29)
        spec = retention.spec_for(self.w["borrower"])
        self.assertEqual(list(retention.due_queryset(spec)), [])

    def test_due_once_the_window_has_closed(self):
        borrower = self.archive(self.w["borrower"], days_ago=31)
        spec = retention.spec_for(borrower)
        self.assertEqual([b.pk for b in retention.due_queryset(spec)], [borrower.pk])

    def test_a_live_row_is_never_due(self):
        spec = retention.spec_for(self.w["borrower"])
        Borrower.objects.filter(pk=self.w["borrower"].pk).update(
            archived_at=timezone.now() - timedelta(days=365))
        self.assertEqual(list(retention.due_queryset(spec)), [])

    def test_archived_without_a_stamp_is_kept_not_purged(self):
        """Rows archived before this feature shipped (archived_at is null)."""
        Borrower.objects.filter(pk=self.w["borrower"].pk).update(
            is_active=False, archived_at=None)
        spec = retention.spec_for(self.w["borrower"])
        self.assertEqual(list(retention.due_queryset(spec)), [])

    def test_retention_off_means_nothing_is_ever_due(self):
        self.archive(self.w["borrower"], days_ago=400)
        with self.settings(ARCHIVE_RETENTION_DAYS=0):
            self.assertIsNone(retention.cutoff())
            self.assertEqual(retention.purge(), [])
            self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())


class PurgeTests(TestCase):
    def setUp(self):
        self.w = make_world()

    def archive_long_ago(self, obj, *, days=31):
        spec = retention.spec_for(obj)
        setattr(obj, spec.flag, spec.archived_value)
        obj.save()
        type(obj).objects.filter(pk=obj.pk).update(
            archived_at=timezone.now() - timedelta(days=days))
        obj.refresh_from_db()
        return obj

    def test_purging_a_building_cascades_past_protect(self):
        building = self.archive_long_ago(self.w["building"])
        outcomes = retention.purge(mirror=False)
        self.assertEqual([o.blocked for o in outcomes], [""])
        self.assertFalse(Building.objects.filter(pk=building.pk).exists())
        self.assertFalse(Room.objects.filter(pk=self.w["room"].pk).exists())
        self.assertFalse(Key.objects.filter(pk=self.w["key"].pk).exists())
        self.assertFalse(BorrowTransaction.objects.filter(pk=self.w["tx"].pk).exists())
        # Only what hung below the building: the borrower and the category stay.
        self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())
        self.assertTrue(KeyCategory.objects.filter(pk=self.w["category"].pk).exists())

    def test_a_nullable_child_is_detached_not_deleted(self):
        post = GuardPost.objects.create(name="RET gate", building=self.w["building"])
        self.archive_long_ago(self.w["building"])
        retention.purge(mirror=False)
        post.refresh_from_db()
        self.assertIsNone(post.building_id)

    def test_a_key_still_out_keeps_the_row(self):
        Key.objects.filter(pk=self.w["key"].pk).update(status=Key.Status.BORROWED)
        building = self.archive_long_ago(self.w["building"])
        outcomes = retention.purge(mirror=False)
        self.assertEqual(len(outcomes), 1)
        self.assertIn("still out", outcomes[0].blocked)
        self.assertTrue(Building.objects.filter(pk=building.pk).exists())

    def test_an_open_transaction_keeps_the_row(self):
        BorrowTransaction.objects.filter(pk=self.w["tx"].pk).update(
            status=BorrowTransaction.Status.ACTIVE, returned_at=None)
        borrower = self.archive_long_ago(self.w["borrower"])
        outcomes = retention.purge(mirror=False)
        self.assertIn("open transaction", outcomes[0].blocked)
        self.assertTrue(Borrower.objects.filter(pk=borrower.pk).exists())

    def test_the_purge_is_audited_and_the_audit_row_survives_it(self):
        borrower = self.archive_long_ago(self.w["borrower"])
        label = str(borrower)
        retention.purge(user=self.w["admin"], mirror=False)
        log = ActivityLog.objects.filter(action=ActivityLog.Action.DELETE,
                                         model_name="Borrower").first()
        self.assertIsNotNone(log)
        self.assertIn("Purged borrower", log.description)
        self.assertEqual(log.object_repr, label)
        self.assertFalse(Borrower.objects.filter(pk=borrower.pk).exists())

    def test_dry_run_deletes_nothing(self):
        borrower = self.archive_long_ago(self.w["borrower"])
        outcomes = retention.purge(dry_run=True, mirror=False)
        self.assertTrue(outcomes[0].purged)
        self.assertTrue(Borrower.objects.filter(pk=borrower.pk).exists())

    def test_the_mirror_is_asked_to_delete_the_parent_only(self):
        """Supabase cascades below the parent itself (migration
        keytrack_mirror_cascading_foreign_keys), so one DELETE is enough."""
        calls = []

        class FakeClient:
            def delete(self, table, **filters):
                calls.append((table, filters))
                return True

        borrower = self.archive_long_ago(self.w["borrower"])
        retention.purge(client=FakeClient())
        self.assertEqual(calls, [("accounts_borrower", {"id": f"eq.{borrower.pk}"})])

    def test_days_left_counts_down(self):
        borrower = self.archive_long_ago(self.w["borrower"], days=10)
        self.assertEqual(retention.days_left(borrower), 20)
        self.assertIsNone(retention.days_left(self.w["admin"]))


class PurgeCommandTests(TestCase):
    def setUp(self):
        self.w = make_world()
        Borrower.objects.filter(pk=self.w["borrower"].pk).update(
            is_active=False, archived_at=timezone.now() - timedelta(days=31))

    def run_command(self, *args):
        out = StringIO()
        call_command("purge_archived", *args, stdout=out, stderr=out)
        return out.getvalue()

    def test_dry_run_reports_without_deleting(self):
        output = self.run_command("--dry-run")
        self.assertIn("will be purged", output)
        self.assertIn("Dry run", output)
        self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())

    def test_yes_purges_unattended(self):
        output = self.run_command("--yes", "--no-mirror")
        self.assertIn("purged", output)
        self.assertFalse(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())

    def test_days_overrides_the_window(self):
        output = self.run_command("--dry-run", "--days", "60")
        self.assertIn("Nothing is due", output)

    def test_retention_off_does_nothing(self):
        with self.settings(ARCHIVE_RETENTION_DAYS=0):
            output = self.run_command()
        self.assertIn("purging is off", output)
        self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())


class DeleteForeverViewTests(TestCase):
    """The Archived pages' manual counterpart of the 30-day sweep.

    Same guard, same cascade, same audit row — the only difference is that the
    admin chose not to wait. A live row is never deletable: Archive first, so
    Restore is always the way back from a misclick.
    """

    def setUp(self):
        self.w = make_world()
        self.client.force_login(self.w["admin"])

    def archive(self, obj):
        spec = retention.spec_for(obj)
        setattr(obj, spec.flag, spec.archived_value)
        obj.save()
        obj.refresh_from_db()
        return obj

    def test_archived_borrower_is_deleted_with_their_history(self):
        borrower = self.archive(self.w["borrower"])
        res = self.client.post(reverse("accounts:borrower_delete", args=[borrower.pk]))
        self.assertEqual(res.status_code, 302)
        self.assertFalse(Borrower.objects.filter(pk=borrower.pk).exists())
        self.assertFalse(BorrowTransaction.objects.filter(pk=self.w["tx"].pk).exists())

    def test_a_live_row_cannot_be_deleted(self):
        res = self.client.post(reverse("accounts:borrower_delete",
                                       args=[self.w["borrower"].pk]),
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 422)
        self.assertIn("not archived", res.json()["message"])
        self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())

    def test_a_key_still_out_refuses_the_delete(self):
        Key.objects.filter(pk=self.w["key"].pk).update(status=Key.Status.BORROWED)
        building = self.archive(self.w["building"])
        res = self.client.post(reverse("facilities:building_delete", args=[building.pk]),
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 422)
        self.assertIn("still out", res.json()["message"])
        self.assertTrue(Building.objects.filter(pk=building.pk).exists())

    def test_deleting_a_building_cascades(self):
        building = self.archive(self.w["building"])
        res = self.client.post(reverse("facilities:building_delete", args=[building.pk]),
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 200)
        self.assertFalse(Room.objects.filter(pk=self.w["room"].pk).exists())
        self.assertFalse(Key.objects.filter(pk=self.w["key"].pk).exists())

    def test_get_is_refused(self):
        borrower = self.archive(self.w["borrower"])
        res = self.client.get(reverse("accounts:borrower_delete", args=[borrower.pk]))
        self.assertEqual(res.status_code, 405)
        self.assertTrue(Borrower.objects.filter(pk=borrower.pk).exists())

    def test_a_guard_cannot_delete(self):
        guard = User.objects.create_user("ret-guard", email="ret-guard@x.test",
                                         password="x", role=User.Role.GUARD)
        borrower = self.archive(self.w["borrower"])
        self.client.force_login(guard)
        res = self.client.post(reverse("accounts:borrower_delete", args=[borrower.pk]),
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 403)
        self.assertTrue(Borrower.objects.filter(pk=borrower.pk).exists())

    def test_the_delete_is_audited_with_the_admin_who_pressed_it(self):
        borrower = self.archive(self.w["borrower"])
        self.client.post(reverse("accounts:borrower_delete", args=[borrower.pk]))
        log = ActivityLog.objects.filter(action=ActivityLog.Action.DELETE,
                                         model_name="Borrower").first()
        self.assertEqual(log.user, self.w["admin"])
        self.assertIn("from the Archived page", log.description)

    def test_bulk_delete_skips_what_a_rule_refuses(self):
        other = Borrower.objects.create(id_number="RET-2", first_name="Ben",
                                        last_name="Blocked",
                                        borrower_type=Borrower.BorrowerType.STAFF)
        BorrowTransaction.objects.create(
            transaction_no="RET-TX-2", key=self.w["key"], borrower=other,
            room=self.w["room"], released_by=self.w["admin"],
            status=BorrowTransaction.Status.ACTIVE)
        self.archive(other)
        borrower = self.archive(self.w["borrower"])

        res = self.client.post(reverse("accounts:borrower_bulk"),
                               {"action": "delete", "ids": [borrower.pk, other.pk]},
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertIn("1 borrower(s) deleted for good", body["message"])
        self.assertEqual(len(body["blocked"]), 1)
        self.assertFalse(Borrower.objects.filter(pk=borrower.pk).exists())
        self.assertTrue(Borrower.objects.filter(pk=other.pk).exists())

    def test_bulk_delete_will_not_touch_a_live_row(self):
        res = self.client.post(reverse("accounts:borrower_bulk"),
                               {"action": "delete", "ids": [self.w["borrower"].pk]},
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(res.status_code, 422)
        self.assertTrue(Borrower.objects.filter(pk=self.w["borrower"].pk).exists())

    def test_every_archivable_list_offers_it(self):
        """Each of the five Archived pages has a delete endpoint wired up."""
        for name, obj in (("facilities:building_delete", self.w["building"]),
                          ("facilities:room_delete", self.w["room"]),
                          ("keys:category_delete", self.w["category"]),
                          ("keys:key_delete", self.w["key"]),
                          ("accounts:borrower_delete", self.w["borrower"])):
            with self.subTest(view=name):
                res = self.client.post(reverse(name, args=[obj.pk]),
                                       HTTP_X_REQUESTED_WITH="XMLHttpRequest")
                # Live rows: refused, but the route exists and is admin-gated.
                self.assertEqual(res.status_code, 422)
                self.assertIn("not archived", res.json()["message"])


class MirrorSchemaTests(TestCase):
    """The generated Supabase schema must carry the same rules as the models."""

    def test_rules_match_django(self):
        from supabase_sync.registry import SPECS
        from supabase_sync.serializers import foreign_key_sql

        sql = {s.model_label: foreign_key_sql(s, SPECS) for s in SPECS}
        # Down the purge chain: cascade.
        self.assertIn('references "public"."facilities_building" ("id")\n'
                      '      on delete cascade', sql["facilities.Room"])
        self.assertIn("on delete cascade", sql["transactions.BorrowTransaction"])
        # PROTECT on a row retention never purges: restrict, not cascade.
        self.assertIn("on delete restrict", sql["transactions.GuardShift"])
        # Nullable: set null, matching SET_NULL / a detached PROTECT.
        self.assertIn("on delete set null", sql["facilities.GuardPost"])
        self.assertIn("on delete set null", sql["audit.ActivityLog"])

    def test_the_purge_chain_is_exactly_the_cascading_set(self):
        from supabase_sync.registry import SPECS
        from supabase_sync.serializers import cascade_parents

        self.assertEqual(cascade_parents(SPECS), {
            "accounts.department", "facilities.building", "facilities.room", "facilities.guardpost",
            "keys.keycategory",
            "keys.key", "accounts.borrower", "scheduling.scheduleblock",
            "scheduling.scheduleexception", "transactions.borrowtransaction",
            "scheduling.term", "scheduling.subject", "scheduling.section",
            "scheduling.subjectoffering", "scheduling.curriculumsubject",
        })

    def test_no_shift_is_pulled_into_the_cascade(self):
        """A guard shift must never be deleted by a purge: it is a user's
        record, not master data below a building."""
        from supabase_sync.registry import SPECS
        from supabase_sync.serializers import cascade_parents

        self.assertNotIn("transactions.guardshift", cascade_parents(SPECS))
        self.assertNotIn("accounts.user", cascade_parents(SPECS))
