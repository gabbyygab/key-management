# Build Log — 2026-10-16 — MVP Borrow Management

## What was requested
Guard/Admin login → Borrow Management (search person → select → search available key → confirm),
one person = one key at a time, side panel of currently borrowed keys, Key Management page,
User Management page. Custom Tailwind dashboard (keep Django admin for debug).
Keep Building/Room/Key. Enforce 1-key + keep shifts.

## What was built

### 1. Foundation
- `config/settings.py`: `TIME_ZONE=Asia/Manila`, `LOGIN_URL/REDIRECT`, `AUTHENTICATION_BACKENDS`,
  fixed email config (`EMAIL_BACKEND=console`, was wrong `MAILERS` dict).
- `config/urls.py`: `/` = borrow dashboard, plus `accounts/`, `facilities/`, `keys/`, `transactions/`.
- New per-app `urls.py` with `app_name` for all 4 apps.
- `templates/base.html` now includes `partials/navbar.html` (role links, user/role, logout)
  and `partials/messages.html`; added `403.html`.

### 2. Data layer (migrated OK)
- `accounts/models.py`: added `CUSTODIAN` role, `Meta.ordering`, `is_approved/full_name/has_role`,
  new `Borrower` model (searchable professor/staff/student, `can_borrow`, `active_transaction()`).
  → migration `accounts/0002_*`
- `facilities/models.py`: `Building`, `Room` (unique per building).
  → migration `facilities/0001_initial.py`
- `keys/models.py`: `KeyCategory`, `Key` (unique room+category+copy).
  → migration `keys/0001_initial.py`
- `transactions/models.py`: `GuardShift`, `BorrowTransaction` (MVP fields only).
  → migration `transactions/0001_initial.py`
- Registered all in `admin.py` with search/filter.

### 3. Auth + permissions
- `accounts/backends.py`: `ApprovedUserBackend` (superuser bypass so setup never locks out).
- `accounts/decorators.py` (`role_required`) + `accounts/mixins.py` (`RoleRequiredMixin`).
- `accounts/views.py`: `login_view`, `logout_view`, borrower CRUD + activate toggle.
- Shift views: `shift_start` / `shift_end`, dashboard shows on-duty badge.

### 4. Business logic
- `transactions/services.py`: `borrow_key()` checks active shift, `can_borrow`,
  **1-active-key-per-borrower**, key `AVAILABLE` (row lock via `select_for_update`);
  `return_key()` flips txn → `RETURNED`, key → `AVAILABLE`. `TXN-YYYYMMDD-####` numbering.

### 5. Guard screens
- `GET /` `borrow_dashboard` (`transactions/borrow_dashboard.html`):
  left = person search + select, middle = available-keys search + purpose + Confirm,
  right = currently-borrowed panel with Return button. Blocked 2nd borrow with message.
- `POST /transactions/borrow/confirm/`, `POST /transactions/return/<id>/`.
- `keys/key_list.html` + `key_form.html` + `category_list.html` (search + status filter).
- `accounts/borrower_list.html` + `borrower_form.html` (search by name/ID).
- `transactions/history.html`, `facilities/building_*` minimal.
- `accounts/forms.py`: Tailwind-styled `BorrowerForm/KeyForm/CategoryForm/BuildingForm`.

## Verification (all passed)
- `manage.py check`: 0 issues.
- `manage.py migrate`: applied 4 new migrations OK.
- Service test (throwaway test DB): borrow → `BORROWED`, 2nd borrow blocked
  ("Juan Cruz already has key K-RM303-1. Return it first."), return → `AVAILABLE`,
  re-borrow OK, `TXN-20261016-0001/0002`.
- Page test: `/accounts/login/`, `/`, `/keys/`, `/accounts/borrowers/` all 200 as guard.

## How to run
```powershell
venv\Scripts\activate
python manage.py migrate
python manage.py createsuperuser  # set role=ADMIN, approval_status=APPROVED in /admin/
python manage.py tailwind runserver
# Login → Start shift → / (search P001/Juan) → Confirm K-RM303-1 → Return from right panel
```

## Deferred (per MVP scope)
`KeyAssignment`, `IncidentReport`, `Notification`, `ActivityLog`, self-registration/approval UI,
overdue cron, reports/CSV, Tailwind build/collectstatic, Postgres, tests, `.env/decouple`.

## Files changed/added (key ones)
`config/settings.py`, `config/urls.py`, `accounts/{models,admin,urls,views,forms,backends,decorators,mixins}.py`,
`facilities/{models,admin,urls,views}.py`, `keys/{models,admin,urls,views}.py`,
`transactions/{models,admin,urls,views,services}.py`, `templates/{base,403,partials/*,accounts/*,keys/*,transactions/*,facilities/*}.html`
