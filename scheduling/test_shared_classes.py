"""Shared group classes: one subject every group of a block attends together.

A shared class is an offering filed against the whole block (``BSIT-4D``). The
offering form can file one from a group (creating the whole-block row when it
is missing), a group's own offering of the same subject is merged in, and the
timetable/list pages label the class as shared.
"""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.models import Borrower, User
from facilities.models import Building, Room
from scheduling import planner
from scheduling.curriculum import offerable_subjects
from scheduling.models import ScheduleBlock, Section, Subject, SubjectOffering, Term


def mk_term():
    today = timezone.localdate()
    start = today - timedelta(days=today.weekday())
    return Term.objects.create(name="AY 2026-27 1st", start_date=start,
                               end_date=start + timedelta(days=120))


class SharedClassBase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="share_admin", email="share_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)
        self.term = mk_term()
        building = Building.objects.create(code="SCI", name="Science")
        self.small = Room.objects.create(building=building, room_code="101", capacity=30)
        self.big = Room.objects.create(building=building, room_code="102", capacity=60)
        # Only the groups exist, as in a real import: no BSIT-4D row yet.
        self.g1 = Section.objects.create(program="BSIT", year_level=4, block="D",
                                         group="G1", student_count=25)
        self.g2 = Section.objects.create(program="BSIT", year_level=4, block="D",
                                         group="G2", student_count=20)
        self.subject = Subject.objects.create(code="IT401", title="Capstone 1")
        self.prof = Borrower.objects.create(
            id_number="P-9", first_name="Ana", last_name="Reyes",
            borrower_type=Borrower.BorrowerType.FACULTY)

    def offer(self, section, subject=None, **kwargs):
        return SubjectOffering.objects.create(
            term=self.term, subject=subject or self.subject, section=section,
            instructor=self.prof, meetings_per_week=1, minutes_per_meeting=90,
            **kwargs)

    def post_offering(self, section, *, shared=False, url=None, **extra):
        data = {"term": self.term.pk, "subject": self.subject.pk,
                "section": section.pk, "instructor": self.prof.pk,
                "meetings_per_week": 1, "minutes_per_meeting": 90,
                "priority": 3, "is_active": "on", **extra}
        if shared:
            data["shared"] = "on"
        self.client.force_login(self.admin)
        return self.client.post((url or reverse("scheduling:offering_create")) + "?modal=1",
                                data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")


class FileSharedOfferingTests(SharedClassBase):
    def test_sharing_from_a_group_files_it_on_a_new_whole_block(self):
        response = self.post_offering(self.g1, shared=True)
        self.assertEqual(response.status_code, 201, response.content)
        block = Section.objects.get(program="BSIT", year_level=4, block="D", group="")
        offering = SubjectOffering.objects.get()
        self.assertEqual(offering.section, block)
        self.assertIn("G1 + G2 together", response.content.decode())
        self.assertIn("Whole block BSIT-4D was added", response.content.decode())

    def test_without_sharing_it_stays_on_the_group(self):
        response = self.post_offering(self.g1)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(SubjectOffering.objects.get().section, self.g1)
        self.assertFalse(Section.objects.filter(group="").exists())

    def test_sharing_merges_the_sibling_groups_own_offering(self):
        own = self.offer(self.g2)
        ScheduleBlock.objects.create(
            term=self.term, room=self.small, subject=self.subject, section=self.g2,
            offering=own, title="IT401 · BSIT-4DG2", day_of_week=0,
            start_time="08:00", end_time="09:30")
        response = self.post_offering(self.g1, shared=True)
        self.assertEqual(response.status_code, 201, response.content)
        own.refresh_from_db()
        self.assertFalse(own.is_active)
        self.assertEqual(own.scheduled_count, 0)
        self.assertIn("BSIT-4DG2's own offering", response.content.decode())

    def test_editing_a_group_offering_into_a_shared_one_unschedules_it(self):
        mine = self.offer(self.g1)
        ScheduleBlock.objects.create(
            term=self.term, room=self.small, subject=self.subject, section=self.g1,
            offering=mine, title="IT401 · BSIT-4DG1", day_of_week=0,
            start_time="08:00", end_time="09:30")
        response = self.post_offering(
            self.g1, shared=True,
            url=reverse("scheduling:offering_update", args=[mine.pk]))
        self.assertEqual(response.status_code, 200, response.content)
        mine.refresh_from_db()
        self.assertTrue(mine.section.is_whole_block)
        self.assertEqual(mine.scheduled_count, 0)
        self.assertIn("taken off the timetable", response.content.decode())

    def test_a_group_cannot_take_a_subject_its_block_already_shares(self):
        block, _ = planner.ensure_whole_block(self.g1)
        self.offer(block)
        response = self.post_offering(self.g2)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "already a shared class", status_code=422)

    def test_sharing_needs_a_sibling_group(self):
        lone = Section.objects.create(program="BSIT", year_level=3, block="D", group="G2")
        response = self.post_offering(lone, shared=True)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "no other group to share with", status_code=422)

    def test_an_archived_shared_offering_must_be_restored_not_refiled(self):
        block, _ = planner.ensure_whole_block(self.g1)
        self.offer(block, is_active=False)
        response = self.post_offering(self.g1, shared=True)
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "restore it instead", status_code=422)

    def test_a_merged_group_offering_cannot_be_restored(self):
        own = self.offer(self.g2)
        self.post_offering(self.g1, shared=True)
        response = self.client.post(reverse("scheduling:offering_archive", args=[own.pk]))
        self.assertEqual(response.status_code, 302)
        own.refresh_from_db()
        self.assertFalse(own.is_active)

    def test_bulk_restore_skips_a_merged_offering(self):
        own = self.offer(self.g2)
        self.post_offering(self.g1, shared=True)
        self.client.post(reverse("scheduling:offering_bulk"),
                         {"action": "restore", "ids": [own.pk], "selected": [own.pk]})
        own.refresh_from_db()
        self.assertFalse(own.is_active)


class SharedClassPlanningTests(SharedClassBase):
    def test_whole_block_headcount_adds_up_its_groups(self):
        block, created = planner.ensure_whole_block(self.g1)
        self.assertTrue(created)
        self.assertEqual(block.headcount, 45)

    def test_shared_class_gets_a_room_for_every_group_and_blocks_both(self):
        block, _ = planner.ensure_whole_block(self.g1)
        shared = self.offer(block)
        run = planner.auto_schedule(term=self.term)
        self.assertEqual(run.placed_count, 1)
        placed = shared.scheduled_blocks().get()
        self.assertEqual(placed.room, self.big)   # 45 students do not fit in 101
        for group in (self.g1, self.g2):
            with self.subTest(group=group.code):
                self.assertTrue(planner.section_conflicts(
                    group, placed.day_of_week, placed.start_time, placed.end_time,
                    self.term))

    def test_both_group_timetables_show_the_shared_class(self):
        block, _ = planner.ensure_whole_block(self.g1)
        self.offer(block)
        planner.auto_schedule(term=self.term)
        for group in (self.g1, self.g2):
            with self.subTest(group=group.code):
                rows = planner.get_section_timetable(group, self.term)["blocks"]
                self.assertEqual([r["shared_groups"] for r in rows], ["G1 + G2"])

    def test_group_is_not_offered_a_subject_its_block_shares(self):
        block, _ = planner.ensure_whole_block(self.g1)
        self.offer(block)
        offer = offerable_subjects(section=self.g2, term=self.term)
        self.assertNotIn(self.subject, offer.subjects)

    def test_sharing_info(self):
        info = planner.sharing_info(self.g1)
        self.assertTrue(info["available"])
        self.assertEqual(info["groups"], "G1 + G2")
        block, _ = planner.ensure_whole_block(self.g1)
        self.assertTrue(planner.sharing_info(block)["is_shared"])


class SharedClassPageTests(SharedClassBase):
    def test_timetable_section_picker_is_searchable_and_labels_shared_blocks(self):
        planner.ensure_whole_block(self.g1)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:section_timetable"))
        self.assertContains(response, 'id="tt-section" name="section" data-searchable')
        self.assertContains(response, "BSIT-4D (G1 + G2 together)")

    def test_offering_list_marks_a_shared_class_and_a_group_filter_finds_it(self):
        block, _ = planner.ensure_whole_block(self.g1)
        self.offer(block)
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:offering_list"),
                                   {"term": self.term.pk, "section": self.g2.pk})
        self.assertContains(response, "G1 + G2 together")
        self.assertContains(response, "IT401")

    def test_offerable_subjects_api_reports_sharing(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:offerable_subjects_api"),
                                   {"section": self.g1.pk, "term": self.term.pk})
        self.assertEqual(response.json()["sharing"]["groups"], "G1 + G2")
        self.assertTrue(response.json()["sharing"]["available"])
