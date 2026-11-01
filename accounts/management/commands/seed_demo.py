"""B7.1 — realistic demo data for development and frontend work.

    python manage.py seed_demo            # idempotent: safe to run again
    python manage.py seed_demo --flush    # DEBUG only: wipe app data first

Creates users (printed with their passwords), departments, buildings, rooms,
guard posts, key categories, keys, borrowers, an active term with a
conflict-free timetable, and a little history (returned loans plus one
overdue key) so the dashboard, reports and POS all have something to show.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from accounts.models import Borrower, Department, User
from facilities.models import Building, GuardPost, Room
from keys.models import Key, KeyCategory
from scheduling.models import ScheduleBlock, ScheduleException, Subject, Term
from scheduling.services import find_conflicts
from transactions.models import BorrowTransaction, GuardShift
from transactions.services import generate_transaction_no, mark_overdue

USERS = [
    # username, password, role, first, last, superuser
    ("superadmin", "KeyTrack-super-2026", User.Role.ADMIN, "System", "Owner", True),
    ("admin", "KeyTrack-admin-2026", User.Role.ADMIN, "Ana", "Reyes", False),
    ("guard1", "KeyTrack-guard-2026", User.Role.GUARD, "Ben", "Cruz", False),
    ("guard2", "KeyTrack-guard-2026", User.Role.GUARD, "Carla", "Santos", False),
]

DEPARTMENTS = [("CCS", "College of Computer Studies"), ("COE", "College of Engineering"),
               ("CAS", "College of Arts and Sciences"), ("ADM", "Administration")]

BUILDINGS = [
    ("CCS", "Computer Studies Building", [
        ("101", "Lecture Room 101", Room.RoomType.CLASSROOM, 1, 40),
        ("102", "Lecture Room 102", Room.RoomType.CLASSROOM, 1, 40),
        ("201", "Programming Lab 1", Room.RoomType.LABORATORY, 2, 35),
        ("202", "Programming Lab 2", Room.RoomType.LABORATORY, 2, 35),
        ("203", "Networking Lab", Room.RoomType.LABORATORY, 2, 30),
        ("301", "Faculty Office", Room.RoomType.OFFICE, 3, 10),
        ("302", "Dean's Office", Room.RoomType.OFFICE, 3, 6),
    ]),
    ("ENG", "Engineering Hall", [
        ("101", "Drafting Room", Room.RoomType.CLASSROOM, 1, 45),
        ("102", "Lecture Room 102", Room.RoomType.CLASSROOM, 1, 50),
        ("103", "Lecture Room 103", Room.RoomType.CLASSROOM, 1, 50),
        ("201", "Electronics Lab", Room.RoomType.LABORATORY, 2, 30),
        ("202", "Physics Lab", Room.RoomType.LABORATORY, 2, 30),
        ("B01", "Generator Room", Room.RoomType.FACILITY, 0, None),
    ]),
    ("SCI", "Science Annex", [
        ("101", "Chemistry Lab", Room.RoomType.LABORATORY, 1, 28),
        ("102", "Biology Lab", Room.RoomType.LABORATORY, 1, 28),
        ("103", "Seminar Room", Room.RoomType.CLASSROOM, 1, 60),
        ("201", "Stock Room", Room.RoomType.FACILITY, 2, None),
        ("202", "AV Room", Room.RoomType.FACILITY, 2, 80),
    ]),
]

CATEGORIES = [("Room", "Regular door key for a room."),
              ("Master", "Opens every room on a floor or building."),
              ("Cabinet", "Equipment or filing cabinet key."),
              ("Office", "Faculty or staff office key.")]

BORROWERS = [
    # id_number, first, last, type, dept
    ("F-1001", "Maria", "Dela Cruz", Borrower.BorrowerType.FACULTY, "CCS"),
    ("F-1002", "Jose", "Rizal", Borrower.BorrowerType.FACULTY, "CCS"),
    ("F-1003", "Andres", "Bonifacio", Borrower.BorrowerType.FACULTY, "COE"),
    ("F-1004", "Gabriela", "Silang", Borrower.BorrowerType.FACULTY, "COE"),
    ("F-1005", "Apolinario", "Mabini", Borrower.BorrowerType.FACULTY, "CAS"),
    ("F-1006", "Melchora", "Aquino", Borrower.BorrowerType.FACULTY, "CAS"),
    ("S-2001", "Teodora", "Alonso", Borrower.BorrowerType.STAFF, "ADM"),
    ("S-2002", "Emilio", "Jacinto", Borrower.BorrowerType.STAFF, "ADM"),
    ("S-2003", "Juan", "Luna", Borrower.BorrowerType.STAFF, "CCS"),
    ("ST-3001", "Leni", "Garcia", Borrower.BorrowerType.STUDENT, "CCS"),
    ("ST-3002", "Paolo", "Mendoza", Borrower.BorrowerType.STUDENT, "COE"),
    ("ST-3003", "Rina", "Villanueva", Borrower.BorrowerType.STUDENT, "CAS"),
    ("ST-3004", "Mark", "Tan", Borrower.BorrowerType.STUDENT, "CCS"),
]

SUBJECTS = [("IT401", "Capstone Project 1", "CCS"), ("CS101", "Introduction to Computing", "CCS"),
            ("CS221", "Data Structures", "CCS"), ("EE201", "Circuits 1", "COE"),
            ("ME110", "Engineering Drawing", "COE"), ("CHEM1", "General Chemistry", "CAS"),
            ("BIO1", "General Biology", "CAS")]

SLOTS = [(time(7, 30), time(9, 0)), (time(9, 0), time(10, 30)),
         (time(10, 30), time(12, 0)), (time(13, 0), time(14, 30))]


class Command(BaseCommand):
    help = "Seed the database with realistic demo data (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--flush", action="store_true",
                            help="Delete ALL key-management data first (DEBUG only).")

    def handle(self, *args, flush=False, **options):
        if flush:
            if not settings.DEBUG:
                raise CommandError("--flush is only allowed with DEBUG=True.")
            self.flush()

        with transaction.atomic():
            users = self.seed_users()
            depts = self.seed_departments()
            rooms = self.seed_facilities()
            self.assign_posts(users)
            keys = self.seed_keys(rooms, users["admin"])
            borrowers = self.seed_borrowers(depts)
            term = self.seed_schedule(rooms, borrowers, depts, users["admin"])
            self.seed_history(keys, borrowers, users)

        self.stdout.write(self.style.SUCCESS("\nDemo data ready. Accounts:"))
        for username, password, role, *_rest in USERS:
            su = " (superuser)" if _rest[-1] else ""
            self.stdout.write(f"  {username:<12} {password:<22} {role}{su}")
        self.stdout.write(f"\nActive term: {term}. Run `python manage.py mark_overdue` any time.")

    # -- pieces ------------------------------------------------------------

    def flush(self):
        from audit.models import ActivityLog
        with transaction.atomic():
            for model in (ActivityLog, BorrowTransaction, GuardShift, ScheduleException,
                          ScheduleBlock, Subject, Term, Key, KeyCategory):
                model.objects.all().delete()
            User.objects.update(assigned_post=None)
            Room.objects.all().delete()
            GuardPost.objects.all().delete()
            Building.objects.all().delete()
            Borrower.objects.all().delete()
            User.objects.filter(username__in=[u[0] for u in USERS]).delete()
            Department.objects.all().delete()
        self.stdout.write(self.style.WARNING("Flushed key-management data."))

    def seed_users(self):
        out = {}
        now = timezone.now()
        for username, password, role, first, last, superuser in USERS:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User(username=username, email=f"{username}@keytrack.local",
                            first_name=first, last_name=last, role=role,
                            approval_status=User.Approval.APPROVED, approved_at=now,
                            is_staff=superuser, is_superuser=superuser,
                            employee_id=f"EMP-{username.upper()}")
                user.set_password(password)
                user.save()
            out[username] = user
        # One pending registration so the approvals badge has something to show.
        User.objects.get_or_create(
            username="applicant", defaults={
                "email": "applicant@keytrack.local", "first_name": "Pending",
                "last_name": "Applicant", "role": User.Role.GUARD,
                "approval_status": User.Approval.PENDING})
        return out

    def seed_departments(self):
        return {code: Department.objects.get_or_create(code=code, defaults={"name": name})[0]
                for code, name in DEPARTMENTS}

    def seed_facilities(self):
        rooms = []
        for code, name, room_rows in BUILDINGS:
            building, _ = Building.objects.get_or_create(code=code, defaults={"name": name})
            for rcode, rname, rtype, floor, cap in room_rows:
                room, _ = Room.objects.get_or_create(
                    building=building, room_code=rcode,
                    defaults={"name": rname, "room_type": rtype, "floor": floor, "capacity": cap})
                rooms.append(room)
        GuardPost.objects.get_or_create(name="Main Gate")
        GuardPost.objects.get_or_create(name="CCS Lobby", defaults={
            "building": Building.objects.get(code="CCS")})
        return rooms

    def assign_posts(self, users):
        posts = {p.name: p for p in GuardPost.objects.all()}
        for username, post in (("guard1", "Main Gate"), ("guard2", "CCS Lobby")):
            u = users[username]
            if u.assigned_post_id is None:
                u.assigned_post = posts[post]
                u.save(update_fields=["assigned_post"])

    def seed_keys(self, rooms, admin):
        cats = {name: KeyCategory.objects.get_or_create(name=name, defaults={"description": d})[0]
                for name, d in CATEGORIES}
        keys = []
        for room in rooms:
            category = cats["Office"] if room.room_type == Room.RoomType.OFFICE else cats["Room"]
            copies = 2 if room.room_type in (Room.RoomType.CLASSROOM, Room.RoomType.LABORATORY) else 1
            for copy in range(1, copies + 1):
                code = f"{room.building.code}-{room.room_code}-{copy}"
                key, _ = Key.objects.get_or_create(
                    key_code=code, defaults={"room": room, "category": category,
                                             "copy_number": copy, "created_by": admin})
                keys.append(key)
        for b_code in ("CCS", "ENG"):
            room = Room.objects.get(building__code=b_code, room_code="101")
            key, _ = Key.objects.get_or_create(
                key_code=f"{b_code}-MASTER", defaults={"room": room, "category": cats["Master"],
                                                       "copy_number": 1, "created_by": admin,
                                                       "description": f"{b_code} floor master"})
            keys.append(key)
        return keys

    def seed_borrowers(self, depts):
        out = []
        today = timezone.localdate()
        for idn, first, last, btype, dept in BORROWERS:
            b, _ = Borrower.objects.get_or_create(
                id_number=idn, defaults={"first_name": first, "last_name": last,
                                         "borrower_type": btype, "department": depts[dept],
                                         "email": f"{first.lower()}.{last.lower().replace(' ', '')}@school.edu"})
            out.append(b)
        # One unauthorized and one expired authorization, for the POS flags.
        Borrower.objects.filter(id_number="ST-3003").update(is_authorized=False)
        Borrower.objects.filter(id_number="ST-3004").update(authorized_until=today - timedelta(days=7))
        return out

    def seed_schedule(self, rooms, borrowers, depts, admin):
        today = timezone.localdate()
        term, _ = Term.objects.get_or_create(
            name="Demo Term", defaults={"start_date": today - timedelta(days=30),
                                        "end_date": today + timedelta(days=120)})
        if not term.is_active:
            term.is_active = True
            term.save(update_fields=["is_active"])
        subjects = [Subject.objects.get_or_create(code=c, defaults={"title": t, "department": depts[d]})[0]
                    for c, t, d in SUBJECTS]
        faculty = [b for b in borrowers if b.borrower_type == Borrower.BorrowerType.FACULTY]
        teaching = [r for r in rooms if r.room_type in (Room.RoomType.CLASSROOM, Room.RoomType.LABORATORY)]

        if ScheduleBlock.objects.filter(term=term).exists():
            return term

        # Each (room, day) gets exactly one slot, so nothing can overlap.
        blocks = []
        for r, room in enumerate(teaching[:5]):
            for day in range(5):
                start, end = SLOTS[(r + day) % len(SLOTS)]
                subject = subjects[(r + day) % len(subjects)]
                blocks.append(ScheduleBlock(
                    term=term, room=room, subject=subject, title=f"{subject.code} Lecture",
                    instructor=faculty[(r + day) % len(faculty)], day_of_week=day,
                    start_time=start, end_time=end, created_by=admin))
        # An all-day open lab today, so the POS schedule warning can be demoed.
        lab = teaching[5] if len(teaching) > 5 else teaching[0]
        if not find_conflicts(lab, today.weekday(), time(7, 0), time(21, 0), term):
            blocks.append(ScheduleBlock(
                term=term, room=lab, title="Open Lab (demo)", instructor=faculty[0],
                day_of_week=today.weekday(), start_time=time(7, 0), end_time=time(21, 0),
                created_by=admin))
        ScheduleBlock.objects.bulk_create(blocks)
        return term

    def seed_history(self, keys, borrowers, users):
        if BorrowTransaction.objects.exists():
            return
        guard = users["guard1"]
        tz = timezone.get_current_timezone()
        today = timezone.localdate()
        usable = [k for k in keys if k.status == Key.Status.AVAILABLE and k.copy_number == 1]
        authorized = [b for b in borrowers if b.can_borrow]

        for days_ago in (3, 2, 1):
            day = today - timedelta(days=days_ago)
            shift = GuardShift.objects.create(guard=guard, post="Main Gate",
                                              is_on_duty=False)
            start = timezone.make_aware(datetime.combine(day, time(7, 0)), tz)
            GuardShift.objects.filter(pk=shift.pk).update(shift_start=start,
                                                          shift_end=start + timedelta(hours=9))
            for n in range(1, 4):
                key = usable[(days_ago * 3 + n) % len(usable)]
                borrower = authorized[(days_ago + n) % len(authorized)]
                out_at = start + timedelta(hours=n * 2)
                txn = BorrowTransaction.objects.create(
                    transaction_no=f"TXN-{day:%Y%m%d}-{n:04d}", key=key, borrower=borrower,
                    room=key.room, released_by=guard, guard_shift=shift,
                    returned_at=out_at + timedelta(hours=1, minutes=30), received_by=guard,
                    return_confirmed=True, status=BorrowTransaction.Status.RETURNED,
                    purpose="Class")
                BorrowTransaction.objects.filter(pk=txn.pk).update(
                    borrowed_at=out_at, expected_return_at=out_at + timedelta(hours=2))

        # One key that should have come back an hour ago -> OVERDUE.
        key = usable[0]
        borrower = authorized[-1]
        out_at = timezone.now() - timedelta(hours=3)
        txn = BorrowTransaction.objects.create(
            transaction_no=generate_transaction_no(), key=key, borrower=borrower,
            room=key.room, released_by=guard, purpose="Lab use")
        BorrowTransaction.objects.filter(pk=txn.pk).update(
            borrowed_at=out_at, expected_return_at=out_at + timedelta(hours=2))
        Key.objects.filter(pk=key.pk).update(status=Key.Status.BORROWED)
        mark_overdue()
