"""Mirror KEYTRACK data to Supabase.

    python manage.py supabase_sync --schema > supabase_schema.sql   # no credentials needed
    python manage.py supabase_sync --dry-run                        # counts only, no network
    python manage.py supabase_sync --check                          # are the tables there?
    python manage.py supabase_sync                                  # push changes
    python manage.py supabase_sync --full --table keys_key          # re-push one table

Pushing is a no-op (exit 0) while SUPABASE_SYNC_ENABLED is False, so it is
safe to schedule before the connector is set up.
"""
from django.core.management.base import BaseCommand, CommandError

from supabase_sync.client import SupabaseClient, SupabaseError
from supabase_sync.conf import get_config
from supabase_sync.registry import select_specs
from supabase_sync.serializers import create_table_sql, serialize, sync_fields
from supabase_sync.services import check_tables, pending_queryset, sync_all


class Command(BaseCommand):
    help = "Push KEYTRACK data to Supabase (one-way mirror). See supabase_sync/README.md."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--schema", action="store_true",
                          help="Print CREATE TABLE SQL for the Supabase SQL editor.")
        mode.add_argument("--dry-run", action="store_true",
                          help="Show what would be pushed without contacting Supabase.")
        mode.add_argument("--check", action="store_true",
                          help="Verify credentials and that every table exists.")
        parser.add_argument("--full", action="store_true",
                            help="Ignore saved watermarks and push every row.")
        parser.add_argument("--table", action="append", dest="tables", metavar="NAME",
                            help="Limit to a table (db_table or app.Model). Repeatable.")

    def handle(self, *args, schema, dry_run, check, full, tables, **options):
        config = get_config()
        try:
            specs = select_specs(tables)
        except LookupError as e:
            raise CommandError(str(e))

        if schema:
            self.stdout.write("-- KEYTRACK -> Supabase mirror. Run once in the Supabase SQL editor.\n")
            for spec in specs:
                self.stdout.write(create_table_sql(spec, config.schema) + "\n")
            return

        if dry_run:
            for spec in specs:
                qs = pending_queryset(spec, full=full)
                self.stdout.write(f"{spec.table_name:<34} {qs.count():>6} row(s)  [{spec.mode}]")
                if options["verbosity"] >= 2 and qs.exists():
                    self.stdout.write(f"    sample: {serialize(qs.first(), sync_fields(spec))}")
            return

        problems = config.problems()
        if check:
            if problems:
                raise CommandError("Not configured: " + " ".join(problems))
            rows = check_tables(tables=tables, client=SupabaseClient(config))
            for table, state in rows:
                style = self.style.SUCCESS if state == "ok" else self.style.ERROR
                self.stdout.write(f"{table:<34} {style(state)}")
            missing = [t for t, s in rows if s != "ok"]
            if missing:
                raise CommandError(f"{len(missing)} table(s) not ready. "
                                   "Run `--schema` and paste the SQL into Supabase.")
            self.stdout.write(self.style.SUCCESS("Supabase is reachable and every table exists."))
            if not config.enabled:
                self.stdout.write("Pushing stays off until SUPABASE_SYNC_ENABLED=True.")
            return

        if not config.enabled:
            self.stdout.write("Supabase sync is disabled (SUPABASE_SYNC_ENABLED=False); nothing pushed.")
            return
        if problems:
            raise CommandError("SUPABASE_SYNC_ENABLED is on but: " + " ".join(problems))

        try:
            results = sync_all(tables=tables, full=full, client=SupabaseClient(config))
        except SupabaseError as e:
            raise CommandError(str(e))
        for r in results:
            if r.ok:
                self.stdout.write(f"{r.table:<34} {self.style.SUCCESS('ok')}  {r.rows} row(s)")
            else:
                self.stdout.write(f"{r.table:<34} {self.style.ERROR('failed')}  "
                                  f"{r.rows} row(s) before: {r.error}")
        failed = [r for r in results if not r.ok]
        if failed:
            raise CommandError(f"{len(failed)} table(s) failed; the others were pushed.")
        self.stdout.write(self.style.SUCCESS(f"Pushed {sum(r.rows for r in results)} row(s)."))
