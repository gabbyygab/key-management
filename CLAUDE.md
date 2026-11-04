# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

KEYTRACK: campus key management. Guards release and receive room keys at a guard post. Borrowers are searchable people, one active key per borrower. A guard's shift starts on login and ends on logout. The stack is Django 6.1 (Python 3.14, `venv/`), SQLite in dev, Tailwind v4 through `django-tailwind-cli` (no npm), and vanilla JS (no framework, no bundler). Lucide icons come from a CDN.

**`guide.md` is the long-form convention guide** (backend patterns, design tokens, modal/pagination rules, verification checklist). Read the matching section before changing a view, form, template or JS file. `tasks/BACKEND_TASKS.txt` and `tasks/FRONTEND_TASKS.txt` are the backlog. Task IDs such as `B5.3` and `F1.2` appear in code comments.

## Commands

Run these in PowerShell with the venv active (`venv\Scripts\activate`), or call `venv\Scripts\python.exe` directly.

```powershell
python manage.py tailwind runserver          # dev server + Tailwind watcher (NOT plain runserver)
python manage.py tailwind build              # one-off CSS build -> static/css/tailwind.css
python manage.py test                        # full suite
python manage.py test transactions           # one app
python manage.py test accounts.test_supabase_auth                      # one module
python manage.py test transactions.test_pos.PosBorrowScheduleTests.test_warn_mode_requires_reason_and_rolls_back   # one test
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py seed_demo [--flush]         # idempotent demo data; prints logins + passwords
python manage.py mark_overdue
python manage.py purge_archived [--dry-run|--days N|--yes|--no-mirror]   # delete rows archived > 30 days
python manage.py import_schedule <csv> --term <id|name> [--dry-run]
python manage.py supabase_sync [--check|--dry-run|--full|--schema|--table keys_key]
```

The database is Supabase Postgres in every environment: set `DATABASE_URL` (session pooler, port 5432) in `.env`, see `.env.example`. `manage.py test` alone uses in-memory SQLite, so tests never touch it. Migration `accounts/0007` creates a built-in `Admin` user whose password is `DEFAULT_USER_PASSWORD` (default `Admin2026`). That user must change the password on first login. `main` is protected, so work on a branch and merge through a PR.

## Architecture

**Apps:** `accounts` (User, Borrower, auth, shared CRUD plumbing, template tags), `facilities` (Building/Room/GuardPost), `keys`, `transactions` (borrow/return, POS, shifts, dashboard), `scheduling` (terms, timetable blocks, conflict checks), `audit` (activity log), `reports`, `supabase_sync`.

- **User ≠ Borrower.** `accounts.User` is a login (role ADMIN/FACILITIES/CUSTODIAN/GUARD/FACULTY_STAFF). `Borrower` is a searchable person who is never a login. Mixing the two up is the most common bug.
- **Rules live in `<app>/services.py`**, with keyword-only arguments, `@transaction.atomic` and `select_for_update()` for race-prone rows. Views only translate HTTP. Do not rewrite `transactions/services.py` `borrow_key`/`return_key`/`pos_borrow`. Wrap them instead.
- **Views never hard-delete.** Foreign keys use `PROTECT`; archive with `is_active` (or `Key.status=INACTIVE`). The *only* delete is the retention purge: archiving stamps `archived_at`, and `manage.py purge_archived` (run daily) deletes a row archived longer than `ARCHIVE_RETENTION_DAYS` (default 30) together with everything below it, cascading past `PROTECT` on purpose. An admin can also skip the wait with **Delete forever** on any Archived page (row button or bulk `action=delete`), which is the same code path. Rules live in `accounts/retention.py` (`ArchiveClockMixin`, `SPECS`, `purge_blocker`, `cascade_delete`, `purge_one`) and `accounts/crud.py` (`delete_forever`, `bulk_delete_forever`, `BULK_ACTIONS`); a hard delete needs the row to be archived first. See guide.md "Archiving has a deadline".
- **Role gating is server-side**, through the `accounts/permissions.py` decorators (`admin_required`, `guard_required`, `admin_or_guard_required`). A template `{% if user.role %}` check is cosmetic only.
- **Shift lifecycle:** `transactions/signals.py` opens and closes a `GuardShift` on `user_logged_in`/`user_logged_out`. `audit/signals.py` logs those events. `ForcePasswordChangeMiddleware` confines a user with `must_change_password` to the password-change page.
- **One view, two audiences (`accounts/crud.py`):** `form_view()` serves the full-page form (no-JS fallback) and the modal partial (`?modal=1` or XHR). A modal POST returns 201/200 with a one-line plain-text message on success, or 422 with the re-rendered partial on failure. It also writes the audit row through `audit.services.log_form_save`. `paginate()` + `querystring_without_page()` are the list helpers.
- **AJAX contract:** 403 (not a redirect) for the wrong role, 422 for rule violations, 201 create, 200 update, `X-CSRFToken` header. JSON APIs live under `/api/borrowers|keys|transactions/` (`<app>/api.py`).
- **`SCHEDULE_ENFORCEMENT`** (`off|warn|block`) controls how the POS treats a borrow that conflicts with the timetable.

## Supabase (backend project)

The project is `gxqljsyzxddujofksykh` (`https://gxqljsyzxddujofksykh.supabase.co`). The **`supabase-keytrack` MCP server** points at this project, so use its tools (`list_tables`, `execute_sql`, `get_advisors`, `query_logs`, `apply_migration`) to inspect the live database. The generic `supabase` MCP server is a different connection. Look before you change anything: the data is live.

The repo has two independent integrations, each switched on by flags in `.env`:

1. **Supabase Auth** (`SUPABASE_AUTH_ENABLED`; see `accounts/SUPABASE_AUTH.md`). `SupabaseAuthBackend` (`accounts/backends.py`) checks passwords against `/auth/v1/token`. Django still owns the session, the role and `is_active`. `accounts/supabase_auth.py` is the urllib client: `sign_in`, and the service-role admin calls `create_user`/`update_user`/`set_banned`/`delete_user`. Admin screens push changes to Supabase and roll the Django write back when the push fails. Existing users are migrated on their first login. With `SUPABASE_AUTH_OFFLINE_FALLBACK`, network failures (and only those) fall back to the local hash. `supabase/only_keytrack_creates_users.sql` installs a trigger on `auth.users` that refuses any account missing `app_metadata.keytrack_provisioned`, so accounts can only be created from KEYTRACK.
2. **One-way data mirror** (`SUPABASE_SYNC_ENABLED`; see `supabase_sync/README.md`). `registry.py` `SPECS` lists the mirrored tables and their modes (`updated`/`append`/`full`). Nothing is ever read back. Supabase tables have RLS on and no policies, so only the service role can use them. To mirror a new model, add a `SyncSpec` (parents before children), re-run `--schema`, and apply the new table's SQL. `supabase_schema.sql` is the generated output. The mirror has real foreign keys — `on delete cascade` down the retention purge chain, `set null` for nullable, `restrict` for `PROTECT` — so `purge_archived` deletes only the parent row there and Postgres clears the rest.

**Tests never reach Supabase.** `config/settings.py` blanks every Supabase setting when `argv[1] == "test"`. Tests that exercise Supabase re-enable it with `override_settings` and a fake client (`accounts/test_supabase_auth.py`, `supabase_sync/tests.py`). Keep it that way.

## Frontend defaults

`scroll-restore.js` loads first, right after the sidebar include: it keeps the sidebar's scroll on every navigation and the page's scroll when a POST, reload or no-JS submit lands back on the same path (opt other scrollers in with `data-preserve-scroll="<key>"`; a key ending in `:page` is restored on the same path only). Then shared JS loads in `base.html` in this order: `csrf → toast → skeletons → modal → modal-form → list-region → confirm → search → date-range-picker → password-strength → button-loading → bulk-actions → navbar → init-icons`. Per-page scripts go in `{% block extra_js %}`. Everything hangs off `window.KeyTrack` / `window.KeyTrackModal`. **After any DOM injection, call `KeyTrack.refreshIcons(el)`.** Otherwise Lucide icons render blank.

**Design tokens are fixed** (`guide.md` §5): `#0F2E1F` sidebar/primary, `#143d28` hover, `#14342A` primary button, `#1E6B4A` accent and focus ring (`--color-accent`), `#FAF7F0` page background, stone grays. Use arbitrary-value classes (`text-[13px]` body, `[15px]` card title, `[22px]` page heading). Cards are `bg-white rounded-xl border border-stone-200 shadow-sm`. Modal cards are `rounded-2xl ... shadow-xl` over a `bg-stone-900/60` overlay. Keep hand-written CSS in `assets/css/source.css` to a minimum. Its unlayered rules (focus ring, skip link, scroll lock, modal animation) win over Tailwind layers on purpose.

**Modals** (`static/js/modal.js`, one delegated controller):
- Markup: `<div id="x" class="hidden fixed inset-0 z-50 ..." role="dialog" aria-modal="true" aria-labelledby="x-title">` containing `<div data-modal-overlay>` and a panel. Open with `[data-modal-open="x"]`. Close with `[data-modal-close]`, an overlay click or Escape (`data-modal-no-escape` opts out). The controller adds a focus trap, focus restore and stacking z-index.
- AJAX form modals: include `partials/ajax_modal_shell.html` (`modal_id`, `modal_size` sm|md|lg|xl, `skeleton_fields`, `skeleton_cols`). Trigger with `<a href="<url>" data-modal-form="<id>" data-modal-refresh="#list-region">`. The server answers with `partials/ajax_form_modal.html`. While loading, the body shows `KeyTrack.skeletons.form()`. Never hand-write a spinner or "Loading…" text. Reference implementation: `templates/accounts/user_list.html`.
- Confirmations go through `KeyTrack.confirm({...})` (`confirm.js`), never `window.confirm`.
- Scroll lock: only `KeyTrackModal.lockScroll()/unlockScroll()` may touch body overflow. They are reference-counted and re-reserve the scrollbar width (`.kt-scroll-locked`).

**Modal animation** (in `source.css`, applied by attribute so every modal gets it for free):
- **In:** overlay `kt-modal-overlay-in`, opacity 0→1, 120ms ease-out. Panel `kt-modal-panel-in`, from `translateY(8px) scale(0.98)` with opacity 0 to rest, 160ms `cubic-bezier(0.16, 1, 0.3, 1)`. CSS only: removing `.hidden` restarts the keyframes, so `modal.js` needs no hook.
- **Out:** there is currently **no exit animation**. `close()` adds `.hidden` immediately, a choice documented in `guide.md` ("closing must feel instant"). Adding an exit animation means changing `modal.js` `close()` to apply a closing class, wait for `animationend` (with a timeout fallback), and only then add `.hidden`, update the stack and restore focus. The same budget applies to any exit animation.
- Budget for all motion: `opacity`/`transform` only, ≤200ms, no bounce or stagger, no JS animation library, and a `prefers-reduced-motion: reduce` opt-out.

**Pagination** (`guide.md` "List pages: pagination"): 10 rows per page (`accounts.crud.LIST_PAGE_SIZE`). Use `page_obj = paginate(request, qs.order_by(..., "-pk"))` so the ordering is total, and pass `querystring_without_page(request)`. Wrap the table plus `{% include "partials/pagination.html" with item_label="keys" %}` in `<div id="..." data-list-region>`. Point the filter form at it with `data-list-region-form="#id"`. `list-region.js` swaps only the region, shows 10 skeleton rows and updates the URL. `{% page_url n %}` (`accounts/templatetags/keytrack.py`) keeps the active filters. Never restyle the footer per page.

**Date Range Picker** (`static/js/date-range-picker.js`, `partials/date_range_picker.html`, `partials/date_range_modal.html`):
- **Contract & Architecture:** Replaces separate unaligned "Date From" and "Date To" `<input type="date">` inputs with a single aligned trigger pill and interactive modal dialog. Behind the scenes, the trigger renders hidden inputs for `start_date` and `end_date` (ISO `YYYY-MM-DD`), preserving standard GET/POST submissions, URL query params, `list-region.js` AJAX swapping, and export links (`reports:export`, `transactions:export_csv`).
- **Template inclusion:**
  ```django
  {% include "partials/date_range_picker.html" with start_date=start_date end_date=end_date %}
  ```
  Optional parameters:
  - `start_name="start_date"` and `end_name="end_date"`: custom input names.
  - `auto_submit=True` (default True): automatically submits the parent form and updates the list region on Apply or Clear. Set `auto_submit=False` if the form has a manual Filter button.
  - `input_bg="bg-white"` (default `bg-[#FAF7F0]`): matches filter toolbar input backgrounds.
  - `placeholder="Select date range"`: default trigger text when no range is set.
- **Modal dialog:** `templates/partials/date_range_modal.html` is included once in `base.html` under `user.is_authenticated` (`id="kt-date-range-modal"`). It opens via `KeyTrackModal.open("kt-date-range-modal")`, using the standard keyboard trap, escape close, scroll lock and 160ms CSS enter animation.
- **Features in modal:**
  - Quick Presets: *Today, Yesterday, Last 7 days, Last 30 days, This month, Last month, This year, All time (clear)*.
  - Interactive month calendar with range selection and prospective hover highlights.
  - Direct date input fallbacks (`#kt-date-modal-start`, `#kt-date-modal-end`) for keyboard-first or precise entry.
  - Live summary readout with day count indicator.
  - Clear button on trigger pill (`[data-date-range-clear]`) for one-click filter reset.
- **JavaScript API:** `window.KeyTrack.DateRangePicker = { init(root), open(pickerEl), clear(pickerEl), formatRange(start, end), parseISO(str), formatISO(date) }`. Fires `date-range:change` event with `{ detail: { start, end } }` when changed. Reference implementations: `templates/transactions/history.html`, `templates/reports/index.html`, `templates/audit/log_list.html`.

## Browser testing with Claude in Chrome

Use the Claude in Chrome tools to check UI changes in the real app after the tests pass:

1. Start `python manage.py tailwind runserver` in the background, then load the `claude-in-chrome` skill and, in one ToolSearch call, the tools you will need: `tabs_context_mcp`, `tabs_create_mcp`, `navigate`, `computer`, `read_page`, `find`, `form_input`, `read_console_messages`, `resize_window`, and `gif_creator` for recordings.
2. Call `tabs_context_mcp` first, then open a **new** tab at `http://127.0.0.1:8000/accounts/login/`. Get credentials from `seed_demo` output or the built-in `Admin` account. Expect the forced password-change redirect on a fresh admin.
3. Check as **both** ADMIN and GUARD. Type admin URLs directly as a guard: they must return 403, because hidden links prove nothing.
4. Resize to **1366×768** (the target lab monitor) for the POS. Its three panes must scroll independently with no page scroll.
5. For modals: Tab stays inside, Escape closes, focus returns to the trigger, the page does not shift behind the modal, the enter animation plays, and a 422 keeps the typed input.
6. Read console messages filtered by pattern, and look for blank icons (a missing `refreshIcons`).
7. Do not trigger native `alert`/`confirm`, because they freeze the extension. `confirm.js` only falls back to `window.confirm` when the confirm modal is missing from the page.

## Graphify function map

The `graphify` skill builds a knowledge graph of the codebase (function and module relationships, call paths, communities). No `graphify-out/` exists yet.

- Build: `/graphify .`. After edits, refresh with `/graphify . --update`. Use `--wiki` for a per-community markdown map.
- Once `graphify-out/graph.json` exists, answer architecture questions with it first: `/graphify query "how does a borrow reach the audit log"`, `/graphify path "pos_borrow" "log_activity"`, `/graphify explain "SupabaseAuthBackend"`.
- Hub nodes worth knowing: `transactions/services.py` (borrow/return/POS), `accounts/crud.py` (`form_view`, `paginate`), `audit/services.py` (`log_activity`, `log_form_save`), `accounts/backends.py` + `supabase_auth.py`, `supabase_sync/registry.py` + `services.py`, `static/js/modal.js` + `modal-form.js`.
- Add `graphify-out/` to `.gitignore` before committing if the graph should stay local.
