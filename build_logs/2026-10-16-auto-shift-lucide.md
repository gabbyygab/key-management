# Build Log — 2026-10-16 — Auto Shift + Guard-Only Login + Lucide Icons

## What was requested
- Guard logs in → shift starts automatically; logout → shift ends.
- Only guards log in.
- Change sidebar icons (no emoji, use an icon package).

## What was built

### 1. Guard-only login
- `accounts/views.py` `login_view`: after `authenticate`, rejects when
  `not (user.is_superuser or user.role == "GUARD")` with
  "Only guard accounts can log in here. Admins use /admin/." No shift is created (rejection happens before `login()`, so the signal never fires).
- All MVP views (`accounts`, `facilities`, `keys`, `transactions`) narrowed to
  `@role_required("GUARD")`; superusers still pass via the decorator bypass.
- `templates/accounts/login.html`: brand panel now "Guard Workspace"; bullets updated
  ("Shift starts on login, ends on logout").

### 2. Automatic shifts via signals (no migration)
- `transactions/signals.py` (new): `user_logged_in` → `get_or_create`-style
  `GuardShift(guard=user, post="Main Desk")` guarded by an active-shift check
  (no duplicates on double login / stale sessions); `user_logged_out` → closes all
  active shifts (`is_on_duty=False`, `shift_end=now`). Post keeps model default
  "Main Desk" — no new field, no UI.
- `transactions/apps.py`: `ready()` imports signals.
- `services.borrow_key/return_key` keep the active-shift check as a safety net.

### 3. Manual shift UI removed, history added
- Deleted `shift_start`/`shift_end` views; new `shift_history` view (guard sees own
  shifts, superuser sees all) at `transactions/shifts/` + `templates/transactions/shift_history.html`
  (ON DUTY / ENDED pills).
- `transactions/urls.py`: `shifts/start|end` → `shifts/` (`shift_history`).
- Sidebar "Guard Shifts" now links to the history page with active-state.
- `dashboard.html` + `borrow_dashboard.html`: Start/End forms replaced with read-only
  badge ("On duty · post · since H:i · ends on logout" / "log out and back in" fallback).

### 4. Lucide icons (package, no emoji)
- `templates/base.html`: Lucide via CDN (`unpkg.com/lucide@latest` + `createIcons()`);
  standalone `login.html` got the same two script tags.
- `partials/sidebar.html`: `layout-dashboard`, `arrow-left-right`, `key-round`,
  `building-2`, `tags`, `users`, `shield-check`, `chart-column`, `log-out` (+ key logo).
- `partials/navbar.html`: `search`, `bell`. `stat_card.html` renders `data-lucide` names;
  dashboard passes `key-round/check/arrow-left-right/clock`. Login: `key-round`,
  `check`, `arrow-right`. Search boxes, `chevron-right`, `arrow-up-right`,
  `download` across borrow/keys/borrowers/history/buildings pages.
- Kept typographic `●` status dots and `›` breadcrumb separators (match the reference pills).

## Verification (throwaway test DB, all passed)
- `manage.py check`: 0 issues.
- Non-guard login → rejected with guard-only message, zero shifts created.
- Guard login → 302 + one active `GuardShift`; second login → still one (no dup).
- `/`, `/transactions/borrow/`, `/transactions/shifts/`, `/keys/`,
  `/accounts/borrowers/` → 200 with `data-lucide`; no emoji codepoints in dashboard HTML.
- Guard logout (POST) → shift `is_on_duty=False`, `shift_end` set.
- Non-guard `force_login` → `/transactions/borrow/` 403.

## How to use
```powershell
venv\Scripts\activate
python manage.py tailwind runserver
# Log in as guard → shift auto-starts → borrow/return → log out → shift auto-ends.
# View shifts: sidebar Guard Shifts. Internet needed once for Lucide CDN icons.
```

## Deferred / notes
- Session expiry without logout leaves the shift open; next login reuses it.
- Per-guard post location not added (all shifts "Main Desk"); easy follow-up via `User` field.
- If offline icon rendering is ever required, vendor the Lucide SVGs inline instead of CDN.
