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
   - has no foreign keys, so a failed table never blocks the others.

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
| `updated` | rooms, keys, terms, schedule blocks, borrow transactions | rows whose `updated_at` is at or after the last pushed value |
| `append` | schedule exceptions, activity log | rows whose `created_at` is at or after the last pushed value |
| `full` | departments, buildings, guard posts, users, borrowers, key categories, subjects, guard shifts | every row on every run; these tables have no change timestamp |

Some data is never sent:
- **Password hashes.** Users use an explicit field allow-list, and the serializer drops `password` as a second safeguard.
- **Sessions, Django auth/admin tables** and uploaded files (only the file path is sent).

**Deletes:** KEYTRACK never hard-deletes. Deactivating a row sets `is_active=False`, and that change mirrors like any other update.

**Adding a model:** add a `SyncSpec` to `SPECS`, then re-run `--schema` and apply the new table's SQL.

## Plugging in a different connector

All network access goes through `client.SupabaseClient`, a small urllib client
for the PostgREST API. `services.py` uses three of its methods:
`upsert(table, rows)`, `select(table, …)` and `probe(table)`.

Any connector that provides those methods can replace it, such as the official
`supabase` Python package or a direct Postgres connection. Pass it as
`sync_all(client=...)` or swap the class. Note that the official package is a
new dependency and needs approval before it is added to `requirements.txt`.
