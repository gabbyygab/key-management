from django.apps import AppConfig


class SupabaseSyncConfig(AppConfig):
    """Optional one-way mirror of KEYTRACK data into a Supabase project.

    Deliberately has no ready() hook and no signals: nothing here touches the
    network unless the `supabase_sync` management command is run with the
    integration configured and enabled. See README.md in this folder.
    """
    default_auto_field = "django.db.models.BigAutoField"
    name = "supabase_sync"
    verbose_name = "Supabase sync"
