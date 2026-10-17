# Build Log — 2026-10-16 — KEYTRACK UI Restyle (`ui key.png`)

## Reference
`ui key.png`: KEYTRACK dashboard — dark-green sidebar (`#0F2E1F`), cream workspace (`#FAF7F0`),
white cards, stat row, stacked building bars, pill statuses, topbar with breadcrumb + search + date + bell + avatar.

## What changed

### Layout shell
- `templates/base.html`: flex shell (fixed `w-64` sidebar + topbar + centered `max-w-[1400px]` main).
  Split auth/anon rendering via `content` / `content_anon` blocks (duplicate `content` blocks are illegal in Django — fixed).
- `templates/partials/sidebar.html` (new): logo, WORKSPACE (Dashboard `/`, Key Transactions `/transactions/borrow/`),
  INVENTORY (Key Inventory, Buildings & Rooms, Key Categories), PEOPLE & SECURITY (Borrowers, Guard Shifts `#shift`),
  MANAGEMENT (Reports & History), user card with initials + role + logout. Active pill = white bg. Mobile: horizontal scroll nav in topbar.
- `templates/partials/navbar.html` (rewritten): breadcrumb, global search → `/keys/`, `{% now %}` date, bell dot, initials avatar.
- `templates/home.html`: anon landing card with login CTA.

### Components
- `partials/stat_card.html`, `partials/status_pill.html` (ACTIVE blue / OVERDUE+LOST red / RETURNED+AVAILABLE green / DAMAGED amber),
  `partials/messages.html` recolored to cream/green/red. `accounts/forms.py` inputs → `border-stone-200` + green focus.
- `templates/403.html`: cream card, back to `dashboard`.

### Dashboard (home `/` is now live, per request)
- `transactions/views.py`: new `dashboard()` — counts (total/available/borrowed/overdue), per-building
  available/borrowed/unavailable (max 8 buildings), recent 6 txns, currently-out 6, active shift.
- `templates/dashboard.html` (new): header (Last-7-days chip, Export → history, Quick Action → transact),
  5 stat cards (4 live + My Shift), CSS stacked bars per building (green `#1E6B4A` / blue `#3B82C4` / stone),
  Currently Out panel, Recent Transactions table with pills.
- `config/urls.py`: `/` → `dashboard` (name `dashboard`); canonical borrow flow at `/transactions/borrow/`;
  removed legacy `/borrow/` alias. `LOGIN_REDIRECT_URL`, login/home redirects → `dashboard`.
- `borrow_confirm` → `/transactions/borrow/?borrower_id=…`; `return/shift` redirects → `transactions:borrow_dashboard`.

### Restyled pages (same URLs/logic, KEYTRACK markup)
- `accounts/login.html`: standalone split screen — dark-green brand panel + cream form card, green focus + `#14342A` button.
- `transactions/borrow_dashboard.html`: header + shift widget, search-person card, release-to card, currently-borrowed card.
- `keys/key_list.html`, `accounts/borrower_list.html`, `transactions/history.html`: header + search + white table cards + pills.
- `*/key_form, borrower_form, category_list, building_list, building_form`: white cards, dark-green Save.

## Verification
- `manage.py check`: 0 issues.
- UI sweep (throwaway test DB, ADMIN user): login contains KEYTRACK branding; 200 + key strings on
  `/`, `/transactions/borrow/`, `/keys/`, `/accounts/borrowers/`, `/transactions/history/`,
  `/facilities/buildings/`, `/keys/categories/`; sidebar green present. Note: `/facilities/buildings/`
  correctly 403s for GUARD role (ADMIN/FACILITIES only) — verified with ADMIN.
- Fixed during build: duplicate `{% block content %}` TemplateSyntaxError; stale `borrow_dashboard` redirects.

## How to view
```powershell
venv\Scripts\activate
python manage.py tailwind runserver
# /accounts/login/ → split KEYTRACK login → / dashboard → sidebar navigation
```

## Deferred
No Chart.js (CSS bars only), no global-search backend (routes to keys), no overdue/incident modules
(sidebar shows only working pages), no Tailwind rebuild (utility classes only).
