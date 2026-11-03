from django.db import migrations, models
from django.utils import timezone


def start_the_clock(apps, schema_editor):
    """See facilities/0003: the window starts at upgrade time, not in the past."""
    apps.get_model("accounts", "Department").objects.filter(
        is_active=False, archived_at__isnull=True).update(archived_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_archive_retention_clock"),
    ]

    operations = [
        migrations.AddField(
            model_name="department",
            name="archived_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(start_the_clock, migrations.RunPython.noop),
    ]
