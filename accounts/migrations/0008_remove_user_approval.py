"""Drop the user-approval columns.

Admins create accounts directly now; there is no pending-approval queue, so
approval_status / approved_by / approved_at have no readers left.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_default_admin'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='user',
            name='approval_status',
        ),
        migrations.RemoveField(
            model_name='user',
            name='approved_at',
        ),
        migrations.RemoveField(
            model_name='user',
            name='approved_by',
        ),
    ]
