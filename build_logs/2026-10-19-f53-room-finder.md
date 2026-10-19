# Build Log — 2026-10-19 — F5.3 Room Finder

## What was requested
`tasks/FRONTEND_TASKS.txt` F5.3: a form for day, start, end and optionally building, room type
and minimum capacity. It calls `GET /api/scheduling/free-rooms/` and shows results as cards
(room code, building, type, capacity) with a "Schedule this" button that opens the schedule
modal (F5.2) pre-filled. "The admin's highest-value daily tool — make it fast and obvious."

## Dependencies that did not exist (built against a stub, per guide.md §3)
- **Backend §5 (B5.1–B5.6):** no `scheduling` app, no `ScheduleBlock`, no `find_free_rooms`, no endpoint.
- **F5.2 schedule modal** and **F1 modal system** (`KeyTrackModal`): not built.
- **B1.1 `admin_required`:** not built, so the views use `role_required("ADMIN")` directly.
- **B8.7:** non-superuser ADMINs still can't log in, so test with a **superuser**.

## What was built
- `scheduling/` is a minimal app with no models and no migrations:
  - `services.py` has `find_free_rooms(*, day_of_week, start_time, end_time, term=None, building=None, room_type=None, min_capacity=None)`.
    **STUB:** it returns every bookable room that matches the filters (active room, active building,
    status not MAINTENANCE/INACTIVE) and ignores day/time because there's no schedule data yet.
    B5.4 replaces the body and keeps the signature.
  - `forms.py` has `FreeRoomQueryForm`, which validates the query. End must be after start, and
    touching times aren't treated as an overlap here.
  - `views.py`:
    - `free_rooms` is the page. A GET with filters renders results server-side, so it works without JS.
    - `free_rooms_api` returns JSON. It sends a 422 with field errors, and a 403 JSON for the wrong role or for anonymous users (never a login redirect).
  - `urls.py` serves `/scheduling/free-rooms/` (`scheduling:free_rooms`) and
    `/api/scheduling/free-rooms/` (`scheduling:free_rooms_api`).
  - `tests.py` has 10 tests covering the contract shape, filters, 422, 403 (guard and anonymous), the page and the no-JS render.
- `templates/scheduling/free_rooms.html` is the page itself:
  - Day pills, start and end times, and duration chips (1h / 1.5h / 2h / 3h).
  - Optional filters, a results grid and an empty state.
  - A fallback `<dialog>` for "Schedule this".
- `templates/scheduling/_free_room_card.html` is one card. The server renders it, and the JS clones it from a `<template>`.
- `static/js/free_rooms.js`:
  - Searches automatically on load and on any change (250 ms debounce), and aborts stale requests.
  - Shows a spinner, disables Find while loading, and checks client-side that end is after start.
  - Shows 422/403 errors inline, keeps the URL in sync so a search can be reloaded or shared, and calls `lucide.createIcons()` after rendering.
- `templates/partials/sidebar.html` adds a "Room Finder" link under Management, shown to admins and superusers only.
- `static/css/tailwind.css` was rebuilt. `manage.py tailwind build` hits a Bun `mkdir EEXIST` bug on OneDrive
  paths, so it was built with the CLI exe directly using the same arguments.

## Contract for F5.2 (schedule modal)
When `window.KeyTrackModal` exists **and** `#schedule-form-modal` is in the page, "Schedule this" does two things:
1. It dispatches `keytrack:schedule-prefill` on `document` with this payload:
   `detail = { room_id, room_label, day_of_week (0=Mon), start_time "HH:MM", end_time "HH:MM" }`
2. It calls `KeyTrackModal.open("schedule-form-modal")`.

Otherwise it opens the built-in fallback dialog, which shows the same values.

## Notes for backend B5.1
`scheduling/` already exists, so **don't run `startapp scheduling`**. Add `models.py`, `admin.py` and
migrations to it instead. Replace the body of `find_free_rooms` and drop `"stub": True` from the API response
(and the amber "Preview" banner from the template).

---

# How to test it, step by step

All commands run from the project root in PowerShell, with the venv active (`venv\Scripts\activate`).

### Step 1: Automated checks
```powershell
python manage.py check                              # expect: no issues
python manage.py makemigrations --check --dry-run   # expect: No changes detected
python manage.py test scheduling                    # expect: Ran 10 tests ... OK
```

### Step 2: Prepare your local database
Your `db.sqlite3` may be unmigrated. Run:
```powershell
python manage.py migrate
```

Then load demo data. Save the block below as `seed_room_finder.py` **outside the repo**
(for example on your Desktop) and run
`python manage.py shell -c "exec(open(r'<path>\seed_room_finder.py').read())"`.
Don't pipe it in with `Get-Content ... | python manage.py shell`: the interactive console
mis-parses the `if` blocks and silently skips creating the accounts.

```python
from accounts.models import User
from facilities.models import Building, Room

if not User.objects.filter(username="admin").exists():
    User.objects.create_superuser("admin", "admin@example.com", "admin12345",
                                  role="ADMIN", approval_status="APPROVED")
if not User.objects.filter(username="guard1").exists():
    User.objects.create_user("guard1", "guard1@example.com", "guard12345",
                             role="GUARD", approval_status="APPROVED")

ccs, _ = Building.objects.get_or_create(code="CCS", defaults={"name": "College of Computing"})
eng, _ = Building.objects.get_or_create(code="ENG", defaults={"name": "Engineering Hall"})
old, _ = Building.objects.get_or_create(code="OLD", defaults={"name": "Old Wing", "is_active": False})

rooms = [
    (ccs, "101", "CLASSROOM", 40, "AVAILABLE", True),
    (ccs, "102", "CLASSROOM", 25, "AVAILABLE", True),
    (ccs, "LAB1", "LABORATORY", 35, "AVAILABLE", True),
    (ccs, "LAB2", "LABORATORY", 30, "MAINTENANCE", True),   # should NOT appear
    (ccs, "FAC-OFF", "OFFICE", 6, "AVAILABLE", True),
    (eng, "201", "CLASSROOM", 60, "AVAILABLE", True),
    (eng, "202", "CLASSROOM", None, "AVAILABLE", True),     # no capacity recorded
    (eng, "GYM", "FACILITY", 200, "AVAILABLE", True),
    (eng, "STORE", "OTHER", 2, "AVAILABLE", False),         # inactive: should NOT appear
    (old, "X1", "CLASSROOM", 30, "AVAILABLE", True),        # inactive building: should NOT appear
]
for building, code, rtype, cap, status, active in rooms:
    Room.objects.update_or_create(building=building, room_code=code, defaults={
        "room_type": rtype, "capacity": cap, "status": status, "is_active": active})

print("Seeded:", Building.objects.count(), "buildings,", Room.objects.count(), "rooms.")
```

### Step 3: Start the server
```powershell
python manage.py tailwind runserver
```
Open http://127.0.0.1:8000/accounts/login/ and log in as **admin / admin12345**.

### Step 4: Find the page
- [ ] The sidebar shows **Room Finder** under *Management*, with an icon (not a blank space).
- [ ] Clicking it opens `/scheduling/free-rooms/`, and the link is highlighted as active.

### Step 5: Defaults and auto-search
- [ ] Today's day pill is selected. Start is the next half hour and end is one hour later.
- [ ] The **1h** chip is highlighted.
- [ ] Results load **without clicking anything**. Expect **7 rooms**: 101, 102, FAC-OFF, LAB1 (CCS) and 201, 202, GYM (ENG).
- [ ] LAB2 (maintenance), STORE (inactive) and X1 (inactive building) are **not** shown.
- [ ] The heading reads e.g. "7 rooms free · Monday 09:00–10:00".
- [ ] Each card shows the room code, building code and name, the type, and capacity ("40 seats", or "Capacity n/a" for 202). Each icon matches the room type.

### Step 6: Filters (each change should refresh results automatically)
- [ ] Building = CCS gives 4 rooms. Set it back to Any building.
- [ ] Room type = Laboratory gives LAB1 only. Set it back to Any type.
- [ ] Min. capacity = 40 gives 101, 201 and GYM (202 drops out because it has no capacity). Typing updates the results after a short pause.
- [ ] Building = ENG with min capacity 100 gives GYM only.
- [ ] Min capacity = 999 shows the "No free rooms for this slot" empty state.

### Step 7: Times and validation
- [ ] Click **2h**: end moves to start + 2 hours, the 2h chip highlights and results refresh.
- [ ] Change the start time: end shifts with it, keeping the same duration.
- [ ] Set end **earlier than or equal to** start: you see a red "End time must be after start time." under End, and no request is sent.
- [ ] Set min capacity to `0` and click Find rooms: the server returns 422 and you see an inline error under Min. capacity.

### Step 8: URL sync and the no-JS fallback
- [ ] After any search, the address bar contains the query (e.g. `?day=0&start=09:00&end=11:00&building=1`).
- [ ] Press F5: the same filters and results come back.
- [ ] Optional: disable JavaScript in DevTools (Ctrl+Shift+P, then "Disable JavaScript"), reload, change a filter and press **Find rooms**. The page still shows results, rendered by the server.

### Step 9: "Schedule this"
- [ ] Click **Schedule this** on LAB1: a dialog opens showing Room `CCS-LAB1` and the day and time of the **current search**.
- [ ] Escape closes it, clicking the dark backdrop closes it, and Close closes it.
- [ ] After closing, focus returns to the button you clicked, and the page scrolls normally again.
- [ ] Tab inside the dialog: focus stays inside.

### Step 10: Speed and feedback
- [ ] DevTools → Network: change filters quickly. Older requests show as *(canceled)*, and only the last one renders.
- [ ] DevTools → Network → throttling "Slow 3G": while loading, the Find button is disabled with a spinner and the cards fade.

### Step 11: Permissions (type URLs directly; hidden links prove nothing)
Log out, then log in as **guard1 / guard12345** (start a shift if prompted).
- [ ] The sidebar has **no** Room Finder link.
- [ ] Visiting `/scheduling/free-rooms/` gives a **403** page.
- [ ] Visiting `/api/scheduling/free-rooms/?day=0&start=09:00&end=10:00` gives a **403** with `{"error": "You do not have permission."}`.
- [ ] Log out completely and visit the same API URL: you still get a **403** (not a redirect to login).

### Step 12: Layout
- [ ] At 1366×768 (DevTools device toolbar), the form fits without horizontal scroll and the cards show 3 per row.
- [ ] At phone width the cards stack one per row and the day pills wrap.
- [ ] Keyboard only: Tab moves through the day pills (arrow keys switch day), times, chips, filters and Find. A focus ring is always visible.

### Step 13: Before committing
```powershell
git status   # expect only: config/settings.py, config/urls.py, static/css/tailwind.css,
             # templates/partials/sidebar.html, scheduling/, static/js/, templates/scheduling/,
             # build_logs/2026-10-19-f53-room-finder.md. NO db.sqlite3, .env, __pycache__.
```
