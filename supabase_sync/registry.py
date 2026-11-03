"""Which models are mirrored to Supabase, and how.

Order matters: parents come before children, because the generated schema now
creates real foreign keys between the mirrored tables (serializers
.foreign_key_sql), so a child row cannot be pushed before its parent exists.
Those keys carry `on delete cascade` down the retention purge chain and
`set null` / `restrict` everywhere else — see accounts/retention.py.

Modes:
  updated  push rows whose `updated_at` moved since the last successful run
  append   rows never change after insert; push rows newer than `created_at`
  full     no reliable change timestamp; push every row each run (small tables)
"""
from dataclasses import dataclass, field

from django.apps import apps


@dataclass(frozen=True)
class SyncSpec:
    model_label: str
    mode: str = "full"                 # "updated" | "append" | "full"
    exclude: tuple = ()
    fields: tuple = ()                 # explicit allow-list; empty = all concrete fields
    table: str = ""                    # defaults to the Django db_table
    batch_size: int = 500
    extra: dict = field(default_factory=dict)

    @property
    def model(self):
        return apps.get_model(self.model_label)

    @property
    def table_name(self):
        return self.table or self.model._meta.db_table

    @property
    def cursor_field(self):
        return {"updated": "updated_at", "append": "created_at"}.get(self.mode)


# Never mirrored: password hashes, session data, Django admin/auth tables.
USER_FIELDS = ("id", "username", "first_name", "last_name", "email", "employee_id",
               "contact_number", "role", "department",
               "assigned_post", "is_active", "date_joined", "last_login")

SPECS = (
    SyncSpec("accounts.Department"),
    SyncSpec("facilities.Building"),
    SyncSpec("facilities.Room", mode="updated"),
    SyncSpec("facilities.GuardPost"),
    SyncSpec("accounts.User", fields=USER_FIELDS),
    SyncSpec("accounts.Borrower"),
    SyncSpec("keys.KeyCategory"),
    SyncSpec("keys.Key", mode="updated"),
    SyncSpec("scheduling.Term", mode="updated"),
    SyncSpec("scheduling.Subject"),
    # Sections and the FCFS offering queue (scheduling/planner.py). Both sit
    # above ScheduleBlock, whose section_id / offering_id point at them.
    SyncSpec("scheduling.Section", mode="updated"),
    SyncSpec("scheduling.SubjectOffering", mode="updated"),
    SyncSpec("scheduling.ScheduleBlock", mode="updated"),
    SyncSpec("scheduling.ScheduleException", mode="append"),
    SyncSpec("transactions.GuardShift"),
    SyncSpec("transactions.BorrowTransaction", mode="updated"),
    SyncSpec("audit.ActivityLog", mode="append"),
)


def select_specs(names=None):
    """Filter SPECS by table name or model label (case-insensitive)."""
    if not names:
        return list(SPECS)
    wanted = {n.lower() for n in names}
    chosen = [s for s in SPECS
              if s.table_name.lower() in wanted or s.model_label.lower() in wanted]
    known = {s.table_name.lower() for s in chosen} | {s.model_label.lower() for s in chosen}
    unknown = wanted - known
    if unknown:
        raise LookupError("Unknown table(s): " + ", ".join(sorted(unknown)))
    return chosen
