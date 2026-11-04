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
from scheduling.curriculum import add_subjects
from scheduling.models import (CurriculumSubject, ScheduleBlock, ScheduleException,
                               Section, Subject, SubjectOffering, Term)
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

# Each building's rooms are designated to this department, which is what the
# auto-scheduler's department rule keys on.
BUILDING_DEPARTMENT = {"CCS": "CCS", "ENG": "COE", "SCI": "CAS"}

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

# (program, year, block, groups, department, students). A block with groups
# splits: BSIT-4D also gets BSIT-4DG1 and BSIT-4DG2, which may be taught at the
# same time in different rooms. A block with no groups stays whole.
SECTIONS = [
    ("BSIT", 4, "D", ["G1", "G2"], "CCS", 44),
    ("BSIT", 3, "A", [], "CCS", 38),
    ("BSCE", 2, "B", ["G1", "G2"], "COE", 40),
    ("BSBIO", 1, "C", [], "CAS", 30),
]

# Subjects that only the curriculum uses (kept out of SUBJECTS so the demo
# timetable above keeps cycling through the same subjects it always did).
CURRICULUM_SUBJECTS = [("IT402", "Capstone Project 2", "CCS"),
                       ("IT403", "Systems Administration and Maintenance", "CCS"),
                       ("MST1", "Science, Technology and Society", "CAS")]

# (program, year level, semester, subject codes): what each year takes per
# semester. The offering form narrows its subject list to the slot of the
# picked section and the term's semester.
CURRICULUM = [
    ("BSIT", 4, Term.Semester.FIRST, ["IT401", "IT403", "CS221", "MST1"]),
    ("BSIT", 4, Term.Semester.SECOND, ["IT402"]),
    ("BSIT", 3, Term.Semester.FIRST, ["CS101", "CS221"]),
    ("BSCE", 2, Term.Semester.FIRST, ["EE201", "ME110"]),
    ("BSBIO", 1, Term.Semester.FIRST, ["CHEM1", "BIO1", "MST1"]),
]

# The first-come-first-served queue, in request order:
# (subject code, section code, meetings per week, minutes, room type).
# Block-wide rows (BSIT-4D) are lectures the whole block attends; group rows
# (BSIT-4DG1) are the split laboratory sessions.
OFFERINGS = [
    ("IT401", "BSIT-4D", 2, 90, ""),
    ("CS221", "BSIT-4DG1", 1, 180, Room.RoomType.LABORATORY),
    ("CS221", "BSIT-4DG2", 1, 180, Room.RoomType.LABORATORY),
    ("CS101", "BSIT-3A", 3, 60, ""),
    ("EE201", "BSCE-2B", 2, 90, ""),
    ("ME110", "BSCE-2BG1", 1, 180, Room.RoomType.LABORATORY),
    ("ME110", "BSCE-2BG2", 1, 180, Room.RoomType.LABORATORY),
    ("CHEM1", "BSBIO-1C", 2, 90, ""),
    ("BIO1", "BSBIO-1C", 2, 90, "LECTURE_LAB"),  # 1 lecture + 1 lab
]


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
            self.seed_sections_and_offerings(term, borrowers, depts, users["admin"])
            self.seed_curriculum(depts, users["admin"])
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
                          ScheduleBlock, SubjectOffering, CurriculumSubject, Section,
                          Subject, Term,
                          Key, KeyCategory):
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
        for username, password, role, first, last, superuser in USERS:
            user = User.objects.filter(username=username).first()
            if user is None:
                user = User(username=username, email=f"{username}@keytrack.local",
                            first_name=first, last_name=last, role=role,
                            is_staff=superuser, is_superuser=superuser,
                            employee_id=f"EMP-{username.upper()}")
                user.set_password(password)
                user.save()
            out[username] = user
        return out

    def seed_departments(self):
        return {code: Department.objects.get_or_create(code=code, defaults={"name": name})[0]
                for code, name in DEPARTMENTS}

    def seed_facilities(self):
        rooms = []
        for code, name, room_rows in BUILDINGS:
            building, _ = Building.objects.get_or_create(code=code, defaults={"name": name})
            # Re-seeding has to un-archive what a demo archived, or nothing is
            # schedulable or borrowable afterwards: every room query filters on
            # building__is_active (scheduling.services.find_free_rooms,
            # planner.candidate_rooms), so one archived building hides its whole
            # floor plan. The term is reactivated the same way below.
            if not building.is_active:
                building.is_active = True
                building.archived_at = None
                building.save(update_fields=["is_active", "archived_at"])
            for rcode, rname, rtype, floor, cap in room_rows:
                room, _ = Room.objects.get_or_create(
                    building=building, room_code=rcode,
                    defaults={"name": rname, "room_type": rtype, "floor": floor, "capacity": cap})
                dept_code = BUILDING_DEPARTMENT.get(code)
                if dept_code and room.department_id is None:
                    room.department = Department.objects.filter(code=dept_code).first()
                    room.save(update_fields=["department"])
                if not room.is_active or room.status == Room.Status.INACTIVE:
                    room.is_active = True
                    room.archived_at = None
                    room.status = Room.Status.AVAILABLE
                    room.save(update_fields=["is_active", "archived_at", "status"])
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
            academic_year="2025-2026", semester=Term.Semester.FIRST,
            defaults={"start_date": today - timedelta(days=30),
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

    def seed_sections_and_offerings(self, term, borrowers, depts, admin):
        """Sections (whole blocks plus split groups) and a FCFS offering queue.

        The queue is left *unscheduled* on purpose: the point of the demo is to
        open Auto-schedule, preview the run and watch first-come-first-served
        fill the week. ``requested_at`` is staggered a minute apart so the queue
        order is visible and stable rather than whatever order the rows got
        their primary keys in.
        """
        if SubjectOffering.objects.filter(term=term).exists():
            return

        sections = {}
        for program, year, block, groups, dept, size in SECTIONS:
            whole = Section.objects.get_or_create(
                program=program, year_level=year, block=block, group="",
                defaults={"department": depts[dept], "student_count": size})[0]
            sections[whole.code] = whole
            for g in groups:
                part = Section.objects.get_or_create(
                    program=program, year_level=year, block=block, group=g,
                    defaults={"department": depts[dept],
                              "student_count": (size + 1) // 2})[0]
                sections[part.code] = part

        subjects = {s.code: s for s in Subject.objects.all()}
        faculty = [b for b in borrowers if b.borrower_type == Borrower.BorrowerType.FACULTY]
        if not faculty:
            return
        stamp = timezone.now() - timedelta(hours=2)
        for i, (subject_code, section_code, meetings, minutes, room_type) in enumerate(OFFERINGS):
            subject, section = subjects.get(subject_code), sections.get(section_code)
            if subject is None or section is None:
                continue
            SubjectOffering.objects.get_or_create(
                term=term, subject=subject, section=section,
                defaults={
                    "instructor": faculty[i % len(faculty)],
                    "meetings_per_week": meetings,
                    "minutes_per_meeting": minutes,
                    "room_type": "" if room_type == "LECTURE_LAB" else room_type,
                    "meeting_format": ("LECTURE_LAB" if room_type == "LECTURE_LAB"
                                       else "STANDARD"),
                    "requested_at": stamp + timedelta(minutes=i),
                    "created_by": admin,
                })

    def seed_curriculum(self, depts, admin):
        """A small curriculum per program. Idempotent: add_subjects skips what
        is already in a slot, and a slot the admin emptied is left alone."""
        for code, title, dept in CURRICULUM_SUBJECTS:
            Subject.objects.get_or_create(
                code=code, defaults={"title": title, "department": depts[dept]})
        if CurriculumSubject.objects.exists():
            return
        subjects = {s.code: s for s in Subject.objects.all()}
        for program, year, semester, codes in CURRICULUM:
            for term in Term.objects.filter(semester=semester):
                add_subjects(program=program, year_level=year, term=term,
                             subjects=[subjects[c] for c in codes if c in subjects],
                             user=admin)

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
