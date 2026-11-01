"""Push KEYTRACK rows to Supabase. One direction only: Django is the source
of truth and nothing is ever read back into the local database.

Soft deletes (is_active=False) mirror like any other update. Nothing is ever
hard-deleted in KEYTRACK, so the mirror never needs to delete either.
"""
from dataclasses import dataclass

from django.utils import timezone

from .client import SupabaseClient, SupabaseError
from .models import SyncCursor
from .registry import select_specs
from .serializers import serialize, sync_fields


@dataclass
class SyncResult:
    table: str
    rows: int = 0
    error: str = ""
    skipped_reason: str = ""

    @property
    def ok(self):
        return not self.error


def pending_queryset(spec, *, full=False):
    """Rows the next run would push for this spec."""
    qs = spec.model._default_manager.all()
    cursor_field = spec.cursor_field
    if cursor_field:
        if not full:
            watermark = (SyncCursor.objects.filter(table=spec.table_name)
                         .values_list("watermark", flat=True).first())
            if watermark:
                qs = qs.filter(**{f"{cursor_field}__gte": watermark})
        return qs.order_by(cursor_field, "pk")
    return qs.order_by("pk")


def _batches(qs, size):
    batch = []
    for obj in qs.iterator(chunk_size=size):
        batch.append(obj)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def sync_spec(spec, client, *, full=False):
    """Push one table. The watermark advances after every successful batch,
    so a failure part-way keeps the progress already made."""
    fields = sync_fields(spec)
    cursor, _ = SyncCursor.objects.get_or_create(table=spec.table_name)
    result = SyncResult(table=spec.table_name)
    try:
        for batch in _batches(pending_queryset(spec, full=full), spec.batch_size):
            result.rows += client.upsert(spec.table_name, [serialize(o, fields) for o in batch])
            if spec.cursor_field:
                cursor.watermark = getattr(batch[-1], spec.cursor_field)
                cursor.save(update_fields=["watermark"])
    except SupabaseError as e:
        result.error = str(e)
        if e.table_missing:
            result.error += (" — create it first: python manage.py supabase_sync --schema")
    cursor.last_run_at = timezone.now()
    cursor.last_status = SyncCursor.Status.ERROR if result.error else SyncCursor.Status.OK
    cursor.last_error = result.error
    cursor.rows_last_run = result.rows
    cursor.save(update_fields=["last_run_at", "last_status", "last_error", "rows_last_run"])
    return result


def sync_all(*, tables=None, full=False, client=None):
    """Push every registered table (or the named ones). Each table is
    independent: one failing does not stop the rest."""
    client = client or SupabaseClient()
    return [sync_spec(spec, client, full=full) for spec in select_specs(tables)]


def check_tables(*, tables=None, client=None):
    """[(table, "ok" | "missing" | error message)] — no data is written."""
    client = client or SupabaseClient()
    out = []
    for spec in select_specs(tables):
        try:
            client.probe(spec.table_name)
            out.append((spec.table_name, "ok"))
        except SupabaseError as e:
            out.append((spec.table_name, "missing" if e.table_missing else str(e)))
    return out
