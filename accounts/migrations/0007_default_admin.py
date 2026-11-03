"""Create the built-in KEYTRACK admin (Admin / Admin2026 by default).

Runs once, on `migrate`. It is an ordinary ADMIN-role account, not a Django
superuser, pre-approved and flagged to choose its own password on first
login. Supabase Auth gets the account on that first login, like any account
made before the switch (accounts/backends.py, SupabaseAuthBackend._migrate).

If the username or email is already taken the migration leaves things alone,
so it never overwrites a real account's password.
"""
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.db import migrations


def create_default_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    username = getattr(settings, "DEFAULT_ADMIN_USERNAME", "Admin")
    email = getattr(settings, "DEFAULT_ADMIN_EMAIL", "keytrack.admin@keytrack.local")
    password = getattr(settings, "DEFAULT_USER_PASSWORD", "Admin2026")
    if User.objects.filter(username=username).exists() or User.objects.filter(email__iexact=email).exists():
        return
    User.objects.create(
        username=username, email=email, first_name="KEYTRACK", last_name="Admin",
        password=make_password(password), role="ADMIN", approval_status="APPROVED",
        is_active=True, is_staff=False, is_superuser=False, must_change_password=True,
    )


def remove_default_admin(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    # Only the untouched built-in account: once its password was changed it
    # is someone's real login and stays.
    User.objects.filter(username=getattr(settings, "DEFAULT_ADMIN_USERNAME", "Admin"),
                        must_change_password=True, last_login__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0006_alter_user_managers"),
    ]

    operations = [
        migrations.RunPython(create_default_admin, remove_default_admin),
    ]
