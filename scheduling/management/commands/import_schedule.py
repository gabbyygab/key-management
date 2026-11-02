"""B5.7 — bulk import ScheduleBlocks from CSV.

    python manage.py import_schedule blocks.csv --term "AY 2026-27 Sem 1"
    python manage.py import_schedule blocks.csv --term 3 --dry-run

CSV columns (header row required; building is optional when room codes are
unique across buildings, otherwise use BLDG-ROOM in room_code):

    room_code, day, start, end, subject, instructor_id[, title, building]

day accepts 0-6, Mon..Sun or Monday..Sunday. Every row is validated against
the database AND against the other rows in the file; if anything fails,
nothing is written (one transaction) and every problem is reported.
"""
import csv
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import Borrower
from facilities.models import Room
from scheduling.models import ScheduleBlock, Subject, Term
from scheduling.services import ScheduleConflictError, validate_block

DAYS = {
    "mon": 0, "monday": 0, "tue": 1, "tues": 1, "tuesday": 1, "wed": 2, "wednesday": 2,
    "thu": 3, "thur": 3, "thurs": 3, "thursday": 3, "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5, "sun": 6, "sunday": 6,
}


def parse_day(value):
    v = (value or "").strip().lower()
    if v.isdigit() and 0 <= int(v) <= 6:
        return int(v)
    if v in DAYS:
        return DAYS[v]
    raise ValueError(f"unknown day '{value}'")


def parse_time(value):
    v = (value or "").strip()
    for fmt in ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M%p"):
        try:
            return datetime.strptime(v, fmt).time()
        except ValueError:
            continue
    raise ValueError(f"bad time '{value}'")


class Command(BaseCommand):
    help = "Import schedule blocks from a CSV file, all-or-nothing."

    def add_arguments(self, parser):
        parser.add_argument("csv_path")
        parser.add_argument("--term", required=True, help="Term id or exact name.")
        parser.add_argument("--dry-run", action="store_true",
                            help="Validate everything, write nothing.")

    def resolve_room(self, code, building):
        qs = Room.objects.select_related("building")
        if building:
            return qs.get(building__code__iexact=building, room_code__iexact=code)
        if "-" in code:
            b, _, r = code.partition("-")
            match = qs.filter(building__code__iexact=b, room_code__iexact=r).first()
            if match:
                return match
        matches = list(qs.filter(room_code__iexact=code)[:2])
        if not matches:
            raise Room.DoesNotExist
        if len(matches) > 1:
            raise ValueError(f"room '{code}' exists in several buildings; add a building column")
        return matches[0]

    def handle(self, csv_path, term, dry_run, **options):
        term_obj = (Term.objects.filter(pk=term).first() if term.isdigit()
                    else Term.objects.filter(name=term).first())
        if term_obj is None:
            raise CommandError(f"Term '{term}' not found.")

        try:
            fh = open(csv_path, newline="", encoding="utf-8-sig")
        except OSError as e:
            raise CommandError(str(e))

        errors, blocks = [], []
        with fh:
            reader = csv.DictReader(fh)
            required = {"room_code", "day", "start", "end"}
            missing = required - {c.strip() for c in (reader.fieldnames or [])}
            if missing:
                raise CommandError(f"CSV is missing column(s): {', '.join(sorted(missing))}")

            for line_no, row in enumerate(reader, start=2):
                row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                try:
                    room = self.resolve_room(row["room_code"], row.get("building", ""))
                    day = parse_day(row["day"])
                    start, end = parse_time(row["start"]), parse_time(row["end"])
                    subject = None
                    if row.get("subject"):
                        subject = Subject.objects.filter(code__iexact=row["subject"]).first()
                        if subject is None:
                            raise ValueError(f"unknown subject '{row['subject']}'")
                    instructor = None
                    if row.get("instructor_id"):
                        instructor = Borrower.objects.filter(id_number=row["instructor_id"]).first()
                        if instructor is None:
                            raise ValueError(f"unknown instructor id '{row['instructor_id']}'")
                    title = row.get("title") or (subject.code if subject else f"{room} booking")
                    block = ScheduleBlock(term=term_obj, room=room, subject=subject, title=title,
                                          instructor=instructor, day_of_week=day,
                                          start_time=start, end_time=end, is_active=True)
                    validate_block(block)
                    # Overlaps with rows earlier in this same file.
                    for other_line, other in blocks:
                        if (other.room_id == room.pk and other.day_of_week == day
                                and other.start_time < end and other.end_time > start):
                            raise ValueError(f"overlaps row {other_line} ({other.title})")
                    blocks.append((line_no, block))
                except Room.DoesNotExist:
                    errors.append(f"row {line_no}: room '{row.get('room_code')}' not found")
                except ScheduleConflictError as e:
                    errors.append(f"row {line_no}: conflicts with "
                                  + ", ".join(f"{c.title} ({c.start_time:%H:%M}-{c.end_time:%H:%M})"
                                              for c in e.conflicts))
                except (ValueError, KeyError) as e:
                    errors.append(f"row {line_no}: {e}")

        if errors:
            for msg in errors:
                self.stderr.write(msg)
            raise CommandError(f"{len(errors)} problem(s); nothing was imported.")

        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"Dry run OK: {len(blocks)} block(s) would be imported."))
            return

        with transaction.atomic():
            ScheduleBlock.objects.bulk_create([b for _, b in blocks])
        self.stdout.write(self.style.SUCCESS(f"Imported {len(blocks)} block(s) into {term_obj}."))
