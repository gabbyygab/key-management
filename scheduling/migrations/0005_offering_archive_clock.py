from django.db import migrations, models
from django.utils import timezone


def start_the_clock(apps, schema_editor):
    """Already-archived offerings get today's stamp (see facilities/0003)."""
    apps.get_model("scheduling", "SubjectOffering").objects.filter(
        is_active=False, archived_at__isnull=True).update(archived_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0004_archive_retention_clock'),
    ]

    operations = [
        migrations.AddField(
            model_name='subjectoffering',
            name='archived_at',
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(start_the_clock, migrations.RunPython.noop),
    ]
