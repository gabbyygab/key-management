"""Model instance -> JSON row, and model -> Postgres CREATE TABLE statement."""
import datetime
import decimal
import uuid

SQL_TYPES = {
    "AutoField": "integer",
    "BigAutoField": "bigint",
    "SmallAutoField": "smallint",
    "IntegerField": "integer",
    "PositiveIntegerField": "integer",
    "SmallIntegerField": "smallint",
    "PositiveSmallIntegerField": "smallint",
    "BigIntegerField": "bigint",
    "PositiveBigIntegerField": "bigint",
    "BooleanField": "boolean",
    "CharField": "text",
    "TextField": "text",
    "SlugField": "text",
    "EmailField": "text",
    "URLField": "text",
    "FileField": "text",
    "ImageField": "text",
    "FilePathField": "text",
    "GenericIPAddressField": "text",
    "DateTimeField": "timestamptz",
    "DateField": "date",
    "TimeField": "time",
    "DurationField": "interval",
    "FloatField": "double precision",
    "JSONField": "jsonb",
    "UUIDField": "uuid",
}


def sync_fields(spec):
    """Concrete fields of the spec's model, honouring fields/exclude."""
    fields = spec.model._meta.concrete_fields
    if spec.fields:
        allowed = set(spec.fields)
        fields = [f for f in fields if f.name in allowed or f.attname in allowed]
    if spec.exclude:
        skip = set(spec.exclude)
        fields = [f for f in fields if f.name not in skip and f.attname not in skip]
    # Defence in depth: never ship a password hash even if a spec forgets.
    return [f for f in fields if f.name != "password"]


def to_json_value(value):
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return f"{value.total_seconds()} seconds"
    if isinstance(value, (decimal.Decimal, uuid.UUID)):
        return str(value)
    if hasattr(value, "name"):  # FieldFile
        return value.name or None
    return str(value)


def serialize(instance, fields):
    return {f.attname: to_json_value(getattr(instance, f.attname)) for f in fields}


def column_type(field):
    if field.is_relation:
        return column_type(field.target_field)
    kind = field.get_internal_type()
    if kind == "DecimalField":
        return f"numeric({field.max_digits}, {field.decimal_places})"
    return SQL_TYPES.get(kind, "text")


def _q(name):
    return '"' + name.replace('"', '""') + '"'


def _label(model):
    return f"{model._meta.app_label}.{model._meta.object_name}".lower()


def mirrored_tables(specs):
    """{"app.model": table} for the specs given — the FK whitelist."""
    return {s.model_label.lower(): s.table_name for s in specs}


def cascade_parents(specs):
    """Labels whose deletion is allowed to cascade in the mirror.

    A purge (accounts/retention.py) deletes an archived Building, Room, Key
    Category, Key or Borrower and then, bottom-up, every row with a NOT NULL
    foreign key into it — rooms, keys, borrow transactions, schedule blocks,
    schedule exceptions. Those are the only deletes that ever happen, so they
    are the only ones that get `on delete cascade` here; the closure below
    walks the same chain the purge walks.

    Everything else keeps `on delete restrict`, which is what Django's PROTECT
    means. Without that distinction a deleted User would silently take the
    borrow history with it in Supabase while Django still refuses the delete.
    """
    from accounts.retention import SPECS as ARCHIVE_SPECS

    labels = {s.model_label.lower() for s in ARCHIVE_SPECS}
    models = {s.model_label.lower(): s.model for s in specs}
    changed = True
    while changed:                      # transitive closure, a few passes at most
        changed = False
        for label, model in models.items():
            if label in labels:
                continue
            for f in model._meta.concrete_fields:
                if (f.is_relation and f.related_model is not None and not f.null
                        and _label(f.related_model) in labels):
                    labels.add(label)
                    changed = True
                    break
    return labels


def _fk_target(field, mirrored, cascading):
    """(table, rule) for a mirrored FK, or None when the parent is not mirrored.

    nullable            -> set null   (Django SET_NULL, or a detached PROTECT)
    purge chain         -> cascade    (what retention.cascade_delete does)
    anything else       -> restrict   (Django PROTECT)
    """
    if not field.is_relation or field.related_model is None:
        return None
    table = mirrored.get(_label(field.related_model))
    if not table:
        return None
    if field.null:
        return table, "set null"
    if _label(field.related_model) in cascading:
        return table, "cascade"
    return table, "restrict"


def foreign_key_sql(spec, specs, schema="public"):
    """ALTER TABLE statements adding this table's cascading foreign keys.

    Emitted after every CREATE TABLE (a child cannot reference a parent table
    that does not exist yet) and guarded by a lookup in pg_constraint, since
    Postgres has no `add constraint if not exists`.
    """
    mirrored = mirrored_tables(specs)
    cascading = cascade_parents(specs)
    table = f"{_q(schema)}.{_q(spec.table_name)}"
    out = []
    for f in sync_fields(spec):
        target = _fk_target(f, mirrored, cascading)
        if not target:
            continue
        parent_table, rule = target
        name = f"{spec.table_name}_{f.attname}_fk"
        parent = f"{_q(schema)}.{_q(parent_table)}"
        out.append(
            f"do $$ begin\n"
            f"  if not exists (select 1 from pg_constraint where conname = '{name}') then\n"
            f"    alter table {table} add constraint {_q(name)}\n"
            f"      foreign key ({_q(f.attname)}) references {parent} (\"id\")\n"
            f"      on delete {rule};\n"
            f"  end if;\n"
            f"end $$;"
        )
        idx = _q(f"{spec.table_name}_{f.attname}_idx")
        out.append(f"create index if not exists {idx} on {table} ({_q(f.attname)});")
    return "\n".join(out)


def create_table_sql(spec, schema="public"):
    fields = sync_fields(spec)
    table = f"{_q(schema)}.{_q(spec.table_name)}"
    cols = []
    for f in fields:
        line = f"  {_q(f.attname)} {column_type(f)}"
        if f.primary_key:
            line += " primary key"
        elif not f.null:
            line += " not null"
        cols.append(line)
    stmts = [
        f"-- {spec.model_label} ({spec.mode})",
        f"create table if not exists {table} (\n" + ",\n".join(cols) + "\n);",
        # RLS on with no policies: only the service-role key (server side) can
        # read or write. Add SELECT policies yourself if a dashboard needs them.
        f"alter table {table} enable row level security;",
    ]
    # `create table if not exists` is a no-op on a table that is already there,
    # so a field added to the Django model later (ScheduleBlock.section, say)
    # would never reach the mirror and every upsert would be rejected for the
    # unknown column. Re-adding each column idempotently lets re-running the
    # schema evolve an existing table. Added columns are left nullable: a NOT
    # NULL column cannot be added to a table that already holds rows, and the
    # next push fills them in anyway.
    for f in fields:
        if not f.primary_key:
            stmts.append(f"alter table {table} add column if not exists "
                         f"{_q(f.attname)} {column_type(f)};")
    if spec.cursor_field:
        idx = _q(f"{spec.table_name}_{spec.cursor_field}_idx")
        stmts.append(f"create index if not exists {idx} on {table} ({_q(spec.cursor_field)});")
    return "\n".join(stmts)
