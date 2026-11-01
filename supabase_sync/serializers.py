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
    if spec.cursor_field:
        idx = _q(f"{spec.table_name}_{spec.cursor_field}_idx")
        stmts.append(f"create index if not exists {idx} on {table} ({_q(spec.cursor_field)});")
    return "\n".join(stmts)
