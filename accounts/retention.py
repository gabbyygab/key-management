"""Archive retention: stamp the archive clock, then purge what has run out.

Why this module exists
----------------------
KEYTRACK's delete verb is *archive* (``accounts/crud.py``). Nothing was ever
hard-deleted, for two reasons that are easy to confuse:

1. **The database refuses it.** Every foreign key into master data is
   ``on_delete=PROTECT`` (``Room.building``, ``Key.room``, ``Key.category``,
   ``BorrowTransaction.key/borrower/room``, ``ScheduleBlock.room``, ...), so
   ``building.delete()`` raises ``ProtectedError``.
2. **No code path asks for it.** The UI only ever flips ``is_active`` (or
   ``Key.status`` to ``INACTIVE``); there is no delete view, and the archive
   buttons additionally refuse while a key below the row is still out.

Retention adds the missing second half. Archiving stamps ``archived_at``; a row
that has been archived for ``settings.ARCHIVE_RETENTION_DAYS`` (default 30) is
then hard-deleted by ``manage.py purge_archived``, together with everything
below it. ``cascade_delete`` walks the reverse relations itself and so steps
around PROTECT deliberately: children with a non-nullable FK are deleted
bottom-up (a cascade), children with a nullable FK are detached (set null),
exactly the way Django's own CASCADE / SET_NULL would behave. The Supabase
mirror carries the same rules as real ``on delete cascade`` / ``set null``
constraints, so deleting the parent row there cleans up the copies.

This is destructive and irreversible: purging a building purges its rooms, its
keys and the borrow history of those keys. Restore before the window closes, or
set ``ARCHIVE_RETENTION_DAYS=0`` to keep archiving forever.
"""
from collections import Counter
from dataclasses import dataclass

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone

# ---------------------------------------------------------------------------
# What can be archived, and what "archived" looks like on each model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ArchiveSpec:
    model_label: str
    noun: str
    flag: str                 # field that records the archived state
    archived_value: object    # value of ``flag`` that means "archived"
    live_value: object        # value of ``flag`` that means "live"

    @property
    def model(self):
        from django.apps import apps
        return apps.get_model(self.model_label)

    def archived_filter(self):
        return {self.flag: self.archived_value}

    def is_archived(self, obj):
        return getattr(obj, self.flag) == self.archived_value


SPECS = (
    ArchiveSpec("facilities.Building", "building", "is_active", False, True),
    ArchiveSpec("facilities.Room", "room", "is_active", False, True),
    ArchiveSpec("facilities.GuardPost", "guard post", "is_active", False, True),
    ArchiveSpec("keys.KeyCategory", "key category", "is_active", False, True),
    # Key has no is_active flag: Status.INACTIVE is the archived state, and
    # restoring puts it back to AVAILABLE (keys/services.set_key_archived).
    ArchiveSpec("keys.Key", "key", "status", "INACTIVE", "AVAILABLE"),
    ArchiveSpec("accounts.Borrower", "borrower", "is_active", False, True),
    ArchiveSpec("accounts.Department", "department", "is_active", False, True),
    ArchiveSpec("scheduling.Term", "term", "is_active", False, True),
    ArchiveSpec("scheduling.Subject", "subject", "is_active", False, True),
    ArchiveSpec("scheduling.Section", "section", "is_active", False, True),
    ArchiveSpec("scheduling.SubjectOffering", "offering", "is_active", False, True),
    # A subject taken out of a program's curriculum (scheduling/curriculum.py).
    ArchiveSpec("scheduling.CurriculumSubject", "curriculum subject", "is_active",
                False, True),
)


def spec_for(obj):
    """The ArchiveSpec for a model instance, or None if it is not archivable."""
    label = f"{obj._meta.app_label}.{obj._meta.object_name}".lower()
    for spec in SPECS:
        if spec.model_label.lower() == label:
            return spec
    return None


# ---------------------------------------------------------------------------
# Stamping the clock
# ---------------------------------------------------------------------------


def sync_clock(obj, spec=None, *, now=None):
    """Bring ``obj.archived_at`` in step with its archive flag. True if changed.

    Archiving is not only done by the archive buttons: BuildingForm, RoomForm,
    BorrowerForm and CategoryForm all expose ``is_active``, and KeyForm exposes
    ``status``. The clock has to be right whichever path flipped the flag, so
    ArchiveClockMixin calls this from ``save()``.
    """
    spec = spec or spec_for(obj)
    if spec is None or not hasattr(obj, "archived_at"):
        return False
    if spec.is_archived(obj):
        if obj.archived_at is None:
            obj.archived_at = now or timezone.now()
            return True
    elif obj.archived_at is not None:
        obj.archived_at = None
        return True
    return False


class ArchiveClockMixin(models.Model):
    """Keeps ``archived_at`` honest on every save, whatever flipped the flag.

    Abstract and field-free, so adding it to a model's bases needs no
    migration. The archive helpers still call ``mark_archived`` explicitly —
    this is the net under forms, the Django admin and the shell.
    """

    class Meta:
        abstract = True

    def save(self, *args, update_fields=None, **kwargs):
        if sync_clock(self) and update_fields is not None:
            update_fields = {*update_fields, "archived_at"}
        return super().save(*args, update_fields=update_fields, **kwargs)


def mark_archived(obj, *, archived, now=None):
    """Set/clear ``archived_at`` on ``obj`` in memory.

    Returns the field name(s) the caller must add to ``save(update_fields=...)``
    so the existing archive helpers keep their single UPDATE. The caller owns
    the flag itself (``is_active`` / ``status``); this only owns the clock.
    """
    if not hasattr(obj, "archived_at"):
        return []
    obj.archived_at = (now or timezone.now()) if archived else None
    return ["archived_at"]


def retention_days():
    return getattr(settings, "ARCHIVE_RETENTION_DAYS", 30)


def cutoff(*, days=None, now=None):
    """The ``archived_at`` on or before which a row is due for purging, or
    None when retention is switched off."""
    days = retention_days() if days is None else days
    if not days or days <= 0:
        return None
    return (now or timezone.now()) - timezone.timedelta(days=days)


def due_queryset(spec, *, days=None, now=None):
    """Archived rows of ``spec`` whose retention window has closed.

    Rows archived before this feature shipped have ``archived_at=None`` and are
    never purged: migration ``facilities/0005`` (and its siblings) stamps the
    existing archive so the clock starts at upgrade time, and anything still
    null is treated as "archived, start date unknown" and kept.
    """
    limit = cutoff(days=days, now=now)
    if limit is None:
        return spec.model._default_manager.none()
    return (spec.model._default_manager
            .filter(**spec.archived_filter(), archived_at__isnull=False)
            .filter(archived_at__lte=limit)
            .order_by("pk"))


# ---------------------------------------------------------------------------
# In-flight guard
#
# Retention never destroys a key that is physically out. Archiving already
# refuses that, but a key can go out again after a restore, and a row can be
# archived while a transaction below it is still ACTIVE, so the purge checks
# once more against the live state instead of trusting the archive-time check.
# ---------------------------------------------------------------------------

OUT_KEY_STATUSES = ("BORROWED", "OVERDUE")
OPEN_TRANSACTION_STATUSES = ("ACTIVE", "OVERDUE")


def purge_blocker(obj):
    """Why this archived row must not be purged yet, or None."""
    from keys.models import Key
    from transactions.models import BorrowTransaction

    keys = Key.objects.none()
    label = f"{obj._meta.app_label}.{obj._meta.object_name}"
    if label == "facilities.Building":
        keys = Key.objects.filter(room__building=obj)
    elif label == "facilities.Room":
        keys = Key.objects.filter(room=obj)
    elif label == "keys.KeyCategory":
        keys = Key.objects.filter(category=obj)
    elif label == "keys.Key":
        keys = Key.objects.filter(pk=obj.pk)

    out = keys.filter(status__in=OUT_KEY_STATUSES).count()
    if out:
        return f"{out} key(s) still out"

    open_tx = BorrowTransaction.objects.filter(status__in=OPEN_TRANSACTION_STATUSES)
    if label == "facilities.Building":
        open_tx = open_tx.filter(room__building=obj)
    elif label == "facilities.Room":
        open_tx = open_tx.filter(room=obj)
    elif label == "keys.KeyCategory":
        open_tx = open_tx.filter(key__category=obj)
    elif label == "keys.Key":
        open_tx = open_tx.filter(key=obj)
    elif label == "accounts.Borrower":
        open_tx = open_tx.filter(borrower=obj)
    else:  # pragma: no cover - every spec is covered above
        open_tx = BorrowTransaction.objects.none()

    count = open_tx.count()
    if count:
        return f"{count} open transaction(s)"
    return None


# ---------------------------------------------------------------------------
# The cascade
# ---------------------------------------------------------------------------


def _reverse_relations(model):
    """Child relations pointing at ``model``, excluding many-to-many."""
    return [rel for rel in model._meta.related_objects
            if not rel.many_to_many]


def cascade_delete(obj, counts=None):
    """Hard-delete ``obj`` and everything that points at it.

    PROTECT is a safety net against *accidental* deletes, not a statement that
    the rows must live forever, so the walk is done by hand:

      * a child whose FK is NOT NULL is deleted first, recursively (CASCADE);
      * a child whose FK is nullable is detached instead (SET NULL), because
        that is what the model already asks for elsewhere (``GuardPost
        .building``, ``ScheduleBlock.instructor``) and it keeps unrelated
        history readable.

    Returns a Counter of ``{db_table: rows deleted}``. Call inside a
    transaction; ``purge()`` does.
    """
    counts = Counter() if counts is None else counts
    for rel in _reverse_relations(obj._meta.model):
        field = rel.field
        children = rel.related_model._base_manager.filter(**{field.name: obj})
        if field.null:
            children.update(**{field.name: None})
            continue
        for child in children.iterator():
            cascade_delete(child, counts)
    obj.delete()
    counts[obj._meta.db_table] += 1
    return counts


@dataclass
class PurgeOutcome:
    spec: ArchiveSpec
    label: str
    pk: int
    archived_at: object
    blocked: str = ""
    counts: Counter = None

    @property
    def purged(self):
        return not self.blocked

    @property
    def rows(self):
        return sum((self.counts or Counter()).values())


def purge_one(obj, *, user=None, request=None, dry_run=False, client=None,
              mirror=True, require_archived=True):
    """Delete one row and everything below it. Returns a PurgeOutcome.

    This is the whole delete path: the scheduled sweep (``purge``) and the
    admin's "Delete forever" button (accounts/crud.delete_forever) both come
    through here, so the guard, the cascade, the audit row and the Supabase
    delete can never drift apart.

    ``require_archived`` is the rule that the button leans on: a live row is
    never deletable, only an archived one, so an accidental click still has
    Archive -> Restore in front of it.
    """
    spec = spec_for(obj)
    if spec is None:
        raise ValueError(f"{obj._meta.label} is not archivable.")
    outcome = PurgeOutcome(spec=spec, label=str(obj), pk=obj.pk,
                           archived_at=getattr(obj, "archived_at", None))
    if require_archived and not spec.is_archived(obj):
        outcome.blocked = f"{obj} is not archived"
        return outcome
    why = purge_blocker(obj)
    if why:
        outcome.blocked = why
        return outcome
    if dry_run:
        outcome.counts = Counter({obj._meta.db_table: 1})
        return outcome
    with transaction.atomic():
        _log_purge(user, spec, obj, request=request)
        outcome.counts = cascade_delete(obj)
    if mirror:
        _mirror_delete(spec, outcome.pk, client=client)
    return outcome


def purge(*, days=None, now=None, dry_run=False, user=None, client=None,
          mirror=True):
    """Purge every row whose retention window has closed.

    Each root is its own transaction, so one blocked or failing row never holds
    up the rest. Returns a list of PurgeOutcome (blocked ones included).
    """
    outcomes = []
    for spec in SPECS:
        for obj in list(due_queryset(spec, days=days, now=now)):
            outcomes.append(purge_one(obj, user=user, dry_run=dry_run,
                                      client=client, mirror=mirror))
    return outcomes


def _log_purge(user, spec, obj, *, request=None):
    """Audit row written *before* the delete, while the pk still exists.

    ActivityLog.user is SET_NULL and the log is not a foreign key to the
    purged row, so the record of the purge survives it. ``request`` is set when
    an admin pressed Delete forever, which also records who and from where; the
    scheduled sweep has no request.
    """
    from audit.services import Action, log_activity
    stamp = f"archived {obj.archived_at:%Y-%m-%d} and " if obj.archived_at else ""
    reason = (f"deleted by {user} from the Archived page"
              if request is not None
              else f"past the {retention_days()}-day retention window")
    log_activity(
        user, Action.DELETE, obj=obj, request=request,
        description=f"Purged {spec.noun} {obj} — {stamp}{reason}.",
        changes={"archived_at": [str(obj.archived_at), None]},
    )


def _mirror_delete(spec, pk, *, client=None):
    """Delete the mirrored copy in Supabase; its children cascade there.

    Best effort on purpose: the local purge has already committed, and the
    mirror is rebuilt by ``manage.py supabase_sync`` anyway. A failure is
    logged, not raised.
    """
    import logging

    from supabase_sync.conf import get_config
    from supabase_sync.client import SupabaseClient, SupabaseError

    logger = logging.getLogger(__name__)
    try:
        if client is None:
            if not get_config().enabled:
                return
            client = SupabaseClient()
        client.delete(spec.model._meta.db_table, id=f"eq.{pk}")
    except SupabaseError as e:
        logger.warning("Supabase purge of %s id=%s failed: %s",
                       spec.model._meta.db_table, pk, e)


def summary(outcomes):
    """'2 room(s) purged (9 row(s) in all). 1 kept: 1 key(s) still out.'"""
    purged = [o for o in outcomes if o.purged]
    blocked = [o for o in outcomes if o.blocked]
    parts = []
    if purged:
        parts.append(f"{len(purged)} row(s) purged "
                     f"({sum(o.rows for o in purged)} row(s) in all).")
    if blocked:
        shown = "; ".join(f"{o.label}: {o.blocked}" for o in blocked[:3])
        if len(blocked) > 3:
            shown += f"; and {len(blocked) - 3} more"
        parts.append(f"{len(blocked)} kept — {shown}.")
    return " ".join(parts) or "Nothing due."


# Re-exported for templates/views that want to show the countdown.
def days_left(obj, *, now=None):
    """Days until ``obj`` is purged, or None (not archived / retention off)."""
    limit = retention_days()
    if not limit or limit <= 0 or getattr(obj, "archived_at", None) is None:
        return None
    age = (now or timezone.now()) - obj.archived_at
    return max(0, limit - age.days)


__all__ = ["SPECS", "ArchiveSpec", "ArchiveClockMixin", "spec_for", "sync_clock",
           "mark_archived", "retention_days",
           "cutoff", "due_queryset", "purge_blocker", "cascade_delete",
           "purge_one", "purge",
           "summary", "days_left", "PurgeOutcome"]
