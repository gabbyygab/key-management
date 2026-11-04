from django.db import migrations


def copy_to_terms(apps, schema_editor):
    """The old curriculum was one column per semester. Give every term its own
    copy of the column for its semester, then drop the shared rows."""
    Term = apps.get_model('scheduling', 'Term')
    Entry = apps.get_model('scheduling', 'CurriculumSubject')
    legacy = list(Entry.objects.filter(term__isnull=True))
    rows = []
    for term in Term.objects.exclude(semester=''):
        for e in legacy:
            if e.semester == term.semester:
                rows.append(Entry(
                    term=term, program=e.program, year_level=e.year_level,
                    semester=e.semester, subject_id=e.subject_id, is_active=e.is_active,
                    archived_at=e.archived_at, created_by_id=e.created_by_id))
    Entry.objects.bulk_create(rows)
    Entry.objects.filter(term__isnull=True).delete()


class Migration(migrations.Migration):
    dependencies = [('scheduling', '0011_curriculum_term')]
    operations = [migrations.RunPython(copy_to_terms, migrations.RunPython.noop)]
