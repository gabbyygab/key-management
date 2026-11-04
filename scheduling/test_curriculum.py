"""Tests for program curricula (scheduling/curriculum.py) and how the subject
offering form narrows its subject list to them."""
from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from accounts import retention
from accounts.models import User
from scheduling import curriculum
from scheduling.forms import SubjectOfferingForm
from scheduling.models import CurriculumSubject, Section, Subject, SubjectOffering, Term


def mk_term(semester=Term.Semester.FIRST, year="2026-2027"):
    today = timezone.localdate()
    return Term.objects.create(academic_year=year, semester=semester,
                               start_date=today - timedelta(days=10),
                               end_date=today + timedelta(days=100))


class CurriculumRulesTests(TestCase):
    def setUp(self):
        self.first = mk_term(Term.Semester.FIRST)
        self.second = mk_term(Term.Semester.SECOND)
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.it401 = Subject.objects.create(code="IT401", title="Capstone 1")
        self.it402 = Subject.objects.create(code="IT402", title="Capstone 2")
        self.mst = Subject.objects.create(code="MST1", title="STS")
        self.cs101 = Subject.objects.create(code="CS101", title="Intro")  # 1st-year subject
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.first,
                                subjects=[self.it401, self.mst])
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.second,
                                subjects=[self.it402])
        curriculum.add_subjects(program="BSIT", year_level=1, term=self.first,
                                subjects=[self.cs101])

    def codes(self, offer):
        return [s.code for s in offer.subjects]

    def test_only_the_slot_for_the_terms_semester_is_offered(self):
        offer = curriculum.offerable_subjects(section=self.section, term=self.first)
        self.assertTrue(offer.restricted)
        self.assertEqual(self.codes(offer), ["IT401", "MST1"])
        offer = curriculum.offerable_subjects(section=self.section, term=self.second)
        self.assertEqual(self.codes(offer), ["IT402"])

    def test_a_group_uses_its_blocks_program_and_year(self):
        g1 = Section.objects.create(program="BSIT", year_level=4, block="D", group="G1")
        offer = curriculum.offerable_subjects(section=g1, term=self.first)
        self.assertEqual(self.codes(offer), ["IT401", "MST1"])

    def test_subjects_already_offered_to_the_section_are_hidden(self):
        SubjectOffering.objects.create(term=self.first, subject=self.it401, section=self.section)
        offer = curriculum.offerable_subjects(section=self.section, term=self.first)
        self.assertEqual(self.codes(offer), ["MST1"])
        self.assertEqual(offer.already_offered, 1)
        # ... but only for that section and that term.
        other = Section.objects.create(program="BSIT", year_level=4, block="E")
        self.assertEqual(self.codes(curriculum.offerable_subjects(section=other, term=self.first)),
                         ["IT401", "MST1"])

    def test_an_archived_offering_still_counts_as_added(self):
        SubjectOffering.objects.create(term=self.first, subject=self.mst, section=self.section,
                                       is_active=False)
        offer = curriculum.offerable_subjects(section=self.section, term=self.first)
        self.assertEqual(self.codes(offer), ["IT401"])

    def test_the_edited_offerings_own_subject_stays(self):
        SubjectOffering.objects.create(term=self.first, subject=self.it401, section=self.section)
        offer = curriculum.offerable_subjects(section=self.section, term=self.first,
                                              keep_subject_id=self.it401.pk)
        self.assertEqual(self.codes(offer), ["IT401", "MST1"])

    def test_no_curriculum_falls_back_to_every_subject(self):
        bsce = Section.objects.create(program="BSCE", year_level=2, block="B")
        offer = curriculum.offerable_subjects(section=bsce, term=self.first)
        self.assertFalse(offer.restricted)
        self.assertEqual(len(offer.subjects), 4)
        self.assertIn("No curriculum set", offer.message)

    def test_a_term_without_a_curriculum_lists_every_subject(self):
        legacy = Term.objects.create(name="Old term", start_date=self.first.start_date,
                                     end_date=self.first.end_date)
        offer = curriculum.offerable_subjects(section=self.section, term=legacy)
        self.assertFalse(offer.restricted)
        self.assertEqual(len(offer.subjects), 4)

    def test_a_new_term_copies_the_previous_same_semester_curriculum(self):
        nxt = mk_term(Term.Semester.FIRST, year="2027-2028")
        self.assertEqual(self.codes(curriculum.offerable_subjects(section=self.section, term=nxt)),
                         ["IT401", "MST1"])
        # The copy is its own: editing it leaves the source alone.
        row = CurriculumSubject.objects.get(term=nxt, subject=self.mst, year_level=4)
        curriculum.remove_subject(row)
        curriculum.add_subjects(program="BSIT", year_level=4, term=nxt, subjects=[self.cs101])
        self.assertEqual(self.codes(curriculum.offerable_subjects(section=self.section, term=nxt)),
                         ["CS101", "IT401"])
        self.assertEqual(self.codes(curriculum.offerable_subjects(section=self.section,
                                                                  term=self.first)),
                         ["IT401", "MST1"])

    def test_a_new_term_of_another_semester_does_not_copy(self):
        summer = mk_term(Term.Semester.SUMMER, year="2027-2028")
        self.assertFalse(summer.curriculum.exists())

    def test_an_archived_subject_drops_out_of_the_curriculum(self):
        self.mst.is_active = False
        self.mst.save()
        offer = curriculum.offerable_subjects(section=self.section, term=self.first)
        self.assertEqual(self.codes(offer), ["IT401"])

    def test_add_skips_duplicates_and_restores_a_removed_row(self):
        added, already = curriculum.add_subjects(program="bsit", year_level=4, term=self.first,
                                                 subjects=[self.it401, self.cs101])
        self.assertEqual(added, [self.cs101])
        self.assertEqual(already, [self.it401])

        row = CurriculumSubject.objects.get(program="BSIT", year_level=4, term=self.first,
                                            subject=self.mst)
        curriculum.remove_subject(row)
        row.refresh_from_db()
        self.assertFalse(row.is_active)
        self.assertIsNotNone(row.archived_at)
        added, _ = curriculum.add_subjects(program="BSIT", year_level=4, term=self.first,
                                           subjects=[self.mst])
        self.assertEqual(added, [self.mst])
        row.refresh_from_db()
        self.assertTrue(row.is_active)
        self.assertIsNone(row.archived_at)
        self.assertEqual(CurriculumSubject.objects.filter(subject=self.mst,
                                                          program="BSIT", year_level=4,
                                                          term=self.first).count(), 1)

    def test_subject_allowed(self):
        self.assertIsNone(curriculum.subject_allowed(
            section=self.section, term=self.first, subject=self.it401))
        reason = curriculum.subject_allowed(section=self.section, term=self.first,
                                            subject=self.it402)
        self.assertEqual(reason, "IT402 is not in the BSIT · 4th year · 2026-2027 1st Semester curriculum.")

    def test_board_has_a_card_per_year_for_the_terms_own_curriculum(self):
        board = curriculum.program_board("BSIT", self.first)
        self.assertEqual([y["year"] for y in board], [1, 2, 3, 4])
        year4 = board[3]
        self.assertEqual([s["code"] for s in year4["semesters"]], ["1ST"])
        self.assertEqual(year4["count"], 2)
        self.assertEqual(curriculum.program_board("BSIT", self.second)[3]["count"], 1)

    def test_purging_a_subject_takes_its_curriculum_rows_with_it(self):
        self.it402.is_active = False
        self.it402.save()
        retention.purge_one(self.it402)
        self.assertFalse(CurriculumSubject.objects.filter(subject_id=self.it402.pk).exists())

    def test_a_removed_row_is_purged_after_the_retention_window(self):
        row = CurriculumSubject.objects.get(subject=self.it402)
        curriculum.remove_subject(row)
        CurriculumSubject.objects.filter(pk=row.pk).update(
            archived_at=timezone.now() - timedelta(days=40))
        spec = retention.spec_for(row)
        self.assertEqual(list(retention.due_queryset(spec)), [row])


class OfferingFormCurriculumTests(TestCase):
    def setUp(self):
        self.term = mk_term(Term.Semester.FIRST)
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.it401 = Subject.objects.create(code="IT401", title="Capstone 1")
        self.it402 = Subject.objects.create(code="IT402", title="Capstone 2")
        self.second = mk_term(Term.Semester.SECOND)
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.second,
                                subjects=[self.it402])

    def data(self, subject, **extra):
        return {"term": self.term.pk, "section": self.section.pk, "subject": subject.pk,
                "meetings_per_week": 2, "minutes_per_meeting": 90,
                "meeting_format": "STANDARD", "priority": 3, "is_active": "on", **extra}

    def option_values(self, form):
        return [str(v) for v, _ in form.fields["subject"].widget.choices if v != ""]

    def test_a_subject_from_another_semester_is_refused_with_a_reason(self):
        form = SubjectOfferingForm(self.data(self.it402))
        self.assertFalse(form.is_valid())
        self.assertIn("IT402 is not in the BSIT · 4th year · 2026-2027 1st Semester curriculum.",
                      form.errors["subject"])

    def test_a_curriculum_subject_is_accepted(self):
        form = SubjectOfferingForm(self.data(self.it401))
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_rendered_options_are_narrowed(self):
        form = SubjectOfferingForm(initial={"term": self.term.pk, "section": self.section.pk})
        self.assertEqual(self.option_values(form), [str(self.it401.pk)])

    def test_editing_keeps_a_subject_that_predates_the_curriculum(self):
        legacy = SubjectOffering.objects.create(term=self.term, subject=self.it402,
                                                section=self.section)
        form = SubjectOfferingForm(self.data(self.it402, notes="moved"), instance=legacy)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertIn(str(self.it402.pk), self.option_values(form))

    def test_conflict_on_addition_preserves_subject_option_and_reports_error(self):
        SubjectOffering.objects.create(term=self.term, subject=self.it401, section=self.section)
        form = SubjectOfferingForm(self.data(self.it401))
        self.assertFalse(form.is_valid())
        self.assertIn("IT401 is already offered to BSIT-4D", form.errors["subject"][0])
        self.assertIn(str(self.it401.pk), self.option_values(form))


    def test_offering_create_conflict_modal_returns_422_with_error_header_and_preserved_subject(self):
        from urllib.parse import unquote
        from accounts.models import User
        admin = User.objects.create_user(username="off_admin", email="off_admin@school.edu",
                                         password="pw-Admin-2026", role=User.Role.ADMIN)
        self.client.force_login(admin)
        SubjectOffering.objects.create(term=self.term, subject=self.it401, section=self.section)
        response = self.client.post(
            reverse("scheduling:offering_create") + "?modal=1",
            {"term": self.term.pk, "subject": self.it401.pk, "section": self.section.pk,
             "meetings_per_week": 2, "minutes_per_meeting": 90, "priority": 3, "is_active": "on"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(response.status_code, 422)
        self.assertIn("X-Error-Message", response.headers)
        msg = unquote(response.headers["X-Error-Message"])
        self.assertIn("IT401 is already offered to BSIT-4D", msg)
        self.assertContains(response, 'data-form-error="', status_code=422)
        self.assertContains(response, f'value="{self.it401.pk}" selected', status_code=422)


class CurriculumViewTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username="cur_admin", email="cur_admin@school.edu",
            password="pw-Admin-2026", role=User.Role.ADMIN)
        self.guard = User.objects.create_user(
            username="cur_guard", email="cur_guard@school.edu",
            password="pw-Guard-2026", role=User.Role.GUARD)
        self.term = mk_term(Term.Semester.FIRST)
        self.section = Section.objects.create(program="BSIT", year_level=4, block="D")
        self.it401 = Subject.objects.create(code="IT401", title="Capstone 1")
        self.mst = Subject.objects.create(code="MST1", title="STS")

    def add(self, **data):
        return self.client.post(reverse("scheduling:curriculum_add") + "?modal=1",
                                {"program": "bsit", "year_level": 4, "term": self.term.pk, **data},
                                HTTP_X_REQUESTED_WITH="XMLHttpRequest")

    def test_page_lists_the_program_and_the_terms_curriculum(self):
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:curriculum"),
                                   {"program": "BSIT", "term": self.term.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "BSIT · 4th year")
        self.assertContains(response, "IT401")
        self.assertContains(response, "BSIT-4D")

    def test_modal_add_answers_201_and_saves(self):
        self.client.force_login(self.admin)
        response = self.add(subjects=[self.it401.pk, self.mst.pk])
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.content.decode(),
                         "2 subjects added to BSIT · 4th year · 2026-2027 1st Semester.")
        self.assertEqual(CurriculumSubject.objects.filter(program="BSIT", is_active=True).count(), 2)

    def test_modal_add_of_only_duplicates_is_a_422(self):
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        self.client.force_login(self.admin)
        response = self.add(subjects=[self.it401.pk])
        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "already in BSIT · 4th year · 2026-2027 1st Semester",
                            status_code=422)

    def test_modal_add_with_nothing_picked_is_a_422(self):
        self.client.force_login(self.admin)
        response = self.add()
        self.assertContains(response, "Pick at least one subject.", status_code=422)

    def test_the_add_form_greys_out_what_is_already_in_the_slot(self):
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        self.client.force_login(self.admin)
        response = self.client.get(reverse("scheduling:curriculum_add"),
                                   {"modal": 1, "program": "BSIT", "year_level": 4,
                                    "term": self.term.pk})
        rows = response.context["subject_rows"]
        self.assertEqual({r["subject"].code: r["in_slot"] for r in rows},
                         {"IT401": True, "MST1": False})

    def test_remove_archives_rather_than_deletes(self):
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        row = CurriculumSubject.objects.get()
        self.client.force_login(self.admin)
        response = self.client.post(reverse("scheduling:curriculum_remove", args=[row.pk]))
        self.assertEqual(response.status_code, 302)
        row.refresh_from_db()
        self.assertFalse(row.is_active)

    def test_offerable_subjects_api(self):
        curriculum.add_subjects(program="BSIT", year_level=4, term=self.term,
                                subjects=[self.it401])
        self.client.force_login(self.admin)
        data = self.client.get(reverse("scheduling:offerable_subjects_api"),
                               {"section": self.section.pk, "term": self.term.pk}).json()
        self.assertTrue(data["restricted"])
        self.assertEqual([s["label"] for s in data["subjects"]], ["IT401 - Capstone 1"])
        self.assertIn("program=BSIT", data["curriculum_url"])

    def test_a_guard_is_refused_everything(self):
        row = CurriculumSubject.objects.create(term=self.term, program="BSIT", year_level=4,
                                               semester="1ST", subject=self.it401)
        self.client.force_login(self.guard)
        for method, url in (
            ("get", reverse("scheduling:curriculum")),
            ("get", reverse("scheduling:curriculum_add")),
            ("post", reverse("scheduling:curriculum_remove", args=[row.pk])),
            ("get", reverse("scheduling:offerable_subjects_api")),
        ):
            with self.subTest(url=url):
                self.assertEqual(getattr(self.client, method)(url).status_code, 403)
        row.refresh_from_db()
        self.assertTrue(row.is_active)
