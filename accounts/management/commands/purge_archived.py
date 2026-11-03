"""Hard-delete rows that have been archived longer than the retention window.

    python manage.py purge_archived --dry-run        # what would go, nothing deleted
    python manage.py purge_archived                  # purge (asks to confirm)
    python manage.py purge_archived --yes            # purge unattended (scheduler)
    python manage.py purge_archived --days 60        # override the window once
    python manage.py purge_archived --no-mirror      # skip the Supabase delete

Archiving a building, room, key category, key or borrower stamps
``archived_at`` (accounts/retention.py). Once ARCHIVE_RETENTION_DAYS (default
30) have passed, this command deletes the row *and everything below it* —
rooms, keys, borrow transactions, schedule blocks. It is irreversible.

Rows holding a key that is still out, or with an open transaction underneath,
are kept and reported instead. Rows archived before the feature shipped have no
``archived_at`` and are never purged until they are archived again.

Run it daily (not every 30 days — the window is per row, so a daily run keeps
each row for exactly its 30 days). Windows Task Scheduler, daily at 02:00:

    schtasks /create /tn "KEYTRACK purge archived" /sc daily /st 02:00 ^
      /tr "E:\\Desktop\\key-management\\venv\\Scripts\\python.exe ^
           E:\\Desktop\\key-management\\manage.py purge_archived --yes"
"""
from django.core.management.base import BaseCommand, CommandError

from accounts import retention


class Command(BaseCommand):
    help = ("Delete rows archived longer than ARCHIVE_RETENTION_DAYS, with "
            "everything that points at them. See accounts/retention.py.")

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=None, metavar="N",
                            help="Retention window for this run "
                                 "(default: settings.ARCHIVE_RETENTION_DAYS).")
        parser.add_argument("--dry-run", action="store_true",
                            help="List what is due without deleting anything.")
        parser.add_argument("--yes", action="store_true",
                            help="Do not ask for confirmation (for a scheduler).")
        parser.add_argument("--no-mirror", action="store_true",
                            help="Do not delete the mirrored rows in Supabase.")

    def handle(self, *args, days, dry_run, yes, no_mirror, **options):
        if days is not None and days < 0:
            raise CommandError("--days cannot be negative.")
        window = retention.retention_days() if days is None else days
        if not window:
            self.stdout.write("ARCHIVE_RETENTION_DAYS=0 — purging is off; "
                              "archived rows are kept forever.")
            return

        limit = retention.cutoff(days=days)
        due = [(spec, list(retention.due_queryset(spec, days=days)))
               for spec in retention.SPECS]
        total = sum(len(rows) for _, rows in due)
        self.stdout.write(f"Retention window: {window} day(s) "
                          f"(archived on or before {limit:%Y-%m-%d %H:%M}).")
        if not total:
            self.stdout.write("Nothing is due.")
            return

        for spec, rows in due:
            if rows:
                self.stdout.write(f"\n{spec.noun}(s) due: {len(rows)}")
                for obj in rows:
                    why = retention.purge_blocker(obj)
                    state = self.style.WARNING(f"kept — {why}") if why else "will be purged"
                    self.stdout.write(f"  {obj} (archived {obj.archived_at:%Y-%m-%d}) "
                                      f"{state}")

        if dry_run:
            self.stdout.write("\nDry run: nothing was deleted.")
            return

        if not yes:
            self.stdout.write(self.style.WARNING(
                "\nThis permanently deletes those rows and everything below them "
                "(keys, borrow history, schedule blocks)."))
            if input("Type 'purge' to continue: ").strip().lower() != "purge":
                self.stdout.write("Cancelled; nothing was deleted.")
                return

        outcomes = retention.purge(days=days, mirror=not no_mirror)
        self.stdout.write("")
        for o in outcomes:
            if o.purged:
                detail = ", ".join(f"{t}: {n}" for t, n in sorted(o.counts.items()))
                self.stdout.write(f"{self.style.SUCCESS('purged')} {o.label} "
                                  f"({o.rows} row(s) — {detail})")
            else:
                self.stdout.write(f"{self.style.WARNING('kept')}   {o.label} "
                                  f"— {o.blocked}")
        self.stdout.write("\n" + retention.summary(outcomes))
