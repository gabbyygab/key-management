# supabase_sync

An optional, **one-way mirror** of KEYTRACK data into a Supabase project. Use it
for off-site backup, dashboards, or other apps that read from Supabase.

KEYTRACK's own database stays the source of truth. Nothing is read back from
Supabase, and nothing in the running app touches the network. Data moves only
when you run the `supabase_sync` management command.

**Until it is configured it does nothing.** With `SUPABASE_SYNC_ENABLED=False`
(the default), `python manage.py supabase_sync` prints one line and exits 0.
That means you can put it on a schedule before the connector exists.

## Setup

1. **Create the tables.** This step needs no credentials:

   ```
   python manage.py supabase_sync --schema > supabase_schema.sql
   ```

   Paste the file into the Supabase **SQL editor** and run it. Each table:
   - matches its Django `db_table` (`keys_key`, `transactions_borrowtransaction`, …);
   - has **row level security on with no policies**, so only the service-role key can read or write it;
   - gets real foreign keys to the other mirrored tables (see "Deletes and cascading foreign keys" below);
   - is safe to re-run: every column is also emitted as `add column if not exists`, so a field
     added to a Django model later reaches a table that already exists. Columns added that way
     stay nullable. A field **removed** from Django is never dropped here; drop or relax that
     column yourself, or a leftover `not null` column rejects every new row.

   Add `SELECT` policies yourself if a dashboard needs to read with the anon key.

2. **Add the credentials to `.env`.** Never commit `.env`. The keys are under
   Project Settings → API.

   ```
   SUPABASE_URL=https://<project-ref>.supabase.co
   SUPABASE_SERVICE_ROLE_KEY=<service_role key>     # server-side only
   SUPABASE_ANON_KEY=<anon key>                     # optional, unused by the sync
   SUPABASE_SCHEMA=public                           # must be exposed in API settings
   SUPABASE_TIMEOUT=10
   SUPABASE_SYNC_ENABLED=False
   ```

   The service-role key bypasses RLS. Keep it in `.env` on the server and never
   put it in a template or in JavaScript.

3. **Verify:**

   ```
   python manage.py supabase_sync --check
   ```

   This checks the credentials and confirms every table exists. It writes nothing.

4. **Turn it on and do the first push:**

   ```
   SUPABASE_SYNC_ENABLED=True          # in .env
   python manage.py supabase_sync --full
   ```

5. **Schedule it.** Each run pushes only what changed. Pick one:
   - **Linux cron:** `*/10 * * * * cd /srv/keytrack && venv/bin/python manage.py supabase_sync`
   - **Windows Task Scheduler:** run `venv\Scripts\python.exe manage.py supabase_sync` every 10 minutes, starting in the project folder.

## Command reference

| Command | Network | Writes |
|---|---|---|
| `supabase_sync --schema` | no | no; prints SQL |
| `supabase_sync --dry-run [-v 2]` | no | no; row counts (and a sample row) per table |
| `supabase_sync --check` | yes | no |
| `supabase_sync` | yes | upserts changed rows |
| `supabase_sync --full` | yes | upserts every row |
| `--table keys_key` / `--table keys.Key` | | limits the run to that table; can be repeated |

The command exits non-zero when a table fails. The other tables are still
pushed. Each table's last run, status, error and watermark appear in Django
admin → **Supabase sync → Sync cursors**.

## What is mirrored

The list lives in `registry.py`.

| Mode | Tables | How changes are found |
|---|---|---|
| `updated` | rooms, keys, terms, sections, subject offerings, schedule blocks, borrow transactions | rows whose `updated_at` is at or after the last pushed value |
| `append` | schedule exceptions, activity log | rows whose `created_at` is at or after the last pushed value |
| `full` | departments, buildings, guard posts, users, borrowers, key categories, subjects, guard shifts | every row on every run; these tables have no change timestamp |

Some data is never sent:
- **Password hashes.** Users use an explicit field allow-list, and the serializer drops `password` as a second safeguard.
- **Sessions, Django auth/admin tables** and uploaded files (only the file path is sent).

**Adding a model:** add a `SyncSpec` to `SPECS`, then re-run `--schema` and apply the new table's SQL. Keep parents before children: the schema now has real foreign keys, so a child row cannot be pushed before its parent exists.

## Deletes and cascading foreign keys

Archiving a row sets `is_active=False` (or `Key.status=INACTIVE`) and mirrors like any
other update. The one real delete is the **retention purge**: `python manage.py
purge_archived` hard-deletes a row archived longer than `ARCHIVE_RETENTION_DAYS`
(default 30) together with everything below it. See `accounts/retention.py` and
guide.md "Archiving has a deadline".

`--schema` therefore also emits foreign keys between the mirrored tables, carrying the
same rules as the Django models:

| Rule | When | Example |
|---|---|---|
| `on delete cascade` | NOT NULL foreign key down the purge chain | `keys_key.room_id`, `transactions_borrowtransaction.key_id` |
| `on delete set null` | nullable foreign key (Django `SET_NULL`, or a nullable `PROTECT` the purge detaches) | `facilities_guardpost.building_id`, `audit_activitylog.user_id` |
| `on delete restrict` | `PROTECT` on a parent retention never purges | `transactions_guardshift.guard_id`, `scheduling_scheduleblock.term_id` |

So the purge only has to delete the parent row here —
`SupabaseClient.delete("facilities_building", id="eq.7")` — and Postgres clears the rooms,
keys, transactions and schedule blocks beneath it. The call is best effort: the local
delete has already committed and a failure is logged, not raised, because the next
`supabase_sync` run would repair the mirror anyway. `purge_archived --no-mirror` skips it.

The constraints are idempotent (`do $$ … pg_constraint … $$`), so re-running the schema
is safe. Applied to the live project as migrations `keytrack_archive_retention_columns`
and `keytrack_mirror_cascading_foreign_keys`. Adding them to a project that already has
data needs the data to be free of orphan rows first.

## Plugging in a different connector

All network access goes through `client.SupabaseClient`, a small urllib client
for the PostgREST API. `services.py` uses three of its methods:
`upsert(table, rows)`, `select(table, …)` and `probe(table)`.

Any connector that provides those methods can replace it, such as the official
`supabase` Python package or a direct Postgres connection. Pass it as
`sync_all(client=...)` or swap the class. Note that the official package is a
new dependency and needs approval before it is added to `requirements.txt`.
