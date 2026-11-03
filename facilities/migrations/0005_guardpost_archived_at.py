from django.db import migrations, models
from django.utils import timezone


def start_the_clock(apps, schema_editor):
    """Posts already deactivated start their retention window now (see 0003)."""
    apps.get_model("facilities", "GuardPost").objects.filter(
        is_active=False, archived_at__isnull=True).update(archived_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("facilities", "0004_room_department"),
    ]

    operations = [
        migrations.AddField(
            model_name="guardpost",
            name="archived_at",
            field=models.DateTimeField(blank=True, editable=False, null=True),
        ),
        migrations.RunPython(start_the_clock, migrations.RunPython.noop),
    ]
