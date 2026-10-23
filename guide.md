# GUIDE.md — Working the `tasks/` backlog

Instructions for an AI coding agent implementing tasks from `tasks/BACKEND_TASKS.txt`
and `tasks/FRONTEND_TASKS.txt` in this repo (KEYTRACK, university key management).

Read this file before touching code. It encodes the repo's real conventions —
verified against the source, not assumed.

---

## 1. Ground truth about this repo

| | |
|---|---|
| Framework | **Django 6.1.2** (`requirements.txt`) — the task files say "Django 5.x"; they are wrong. Use 6.1 docs. |
| Python | 3.14 (venv at `venv/`) |
| DB | SQLite dev, `db.sqlite3`, **gitignored** — every dev has their own |
| CSS | Tailwind via `django-tailwind-cli==4.8.1` — `{% tailwind_css %}` in `base.html`, **no npm, no build step** |
| JS | Vanilla, no framework, no bundler |
| Icons | Lucide from CDN, `lucide.createIcons()` in `base.html` |
| Config | `python-decouple` reading `.env` (gitignored; `.env.example` is the template) |
| Apps | `accounts`, `facilities`, `keys`, `transactions`, `audit` (empty), `notifications` (empty) |
| Branch | `main`, protected — **PR only** |
| Remote | `https://github.com/gabbyygab/key-management` |

Run the dev server with `python manage.py tailwind runserver` (not plain `runserver` —
Tailwind must watch).

### Domain model in one paragraph

A **User** is a login (roles: ADMIN, FACILITIES, CUSTODIAN, GUARD, FACULTY_STAFF; plus
`approval_status` PENDING/APPROVED/REJECTED). A **Borrower** is a *searchable person*
(faculty/staff/student) who may collect keys — **not a login**; the two are separate
models and confusing them is the most common mistake in this codebase. A **Key**
belongs to a **Room**, which belongs to a **Building**. A **GuardShift** opens on login
and closes on logout (signals). A **BorrowTransaction** records one key going out to one
borrower during one shift.

---

## 2. Non-negotiable rules

1. **Never commit `db.sqlite3`, `.env`, `venv/`, `media/`, `staticfiles/`, or `__pycache__`.**
   `.gitignore` covers these — verify with `git status` before every commit.
2. **Never rewrite `transactions/services.py`'s `borrow_key()` / `return_key()`.**
   They are `@db_transaction.atomic` with `select_for_update()` row locking and are
   verified correct end-to-end. Wrap them; do not reimplement their rules.
3. **Never hard-delete.** Every FK to Building/Room/Key/Borrower uses
   `on_delete=PROTECT`. Use `is_active` soft-delete and surface a readable error.
4. **Never put business rules in a view.** Rules live in `<app>/services.py`;
   views translate HTTP to service calls and back.
5. **Role gating is server-side.** `{% if user.role == "ADMIN" %}` in a template is
   *honesty*, never *security*. Every view needs its decorator too.
6. **Don't add dependencies** without asking. No htmx, Alpine, React, DRF, or charting
   library — the task files explicitly chose vanilla JS + Django templates.
7. **One task per commit, one section per PR.** See §7.
8. **Don't invent design tokens.** The palette in §5 is fixed.

---

## 3. Which task do I pick up?

Both task files end with a **BUILD ORDER** section. Follow it — the ordering encodes
real dependencies.

**Start here, in this order:**

1. `B8.7` — non-superuser ADMINs currently *cannot log in at all*
   (`accounts/views.py:26`). Nothing admin-facing is testable until this is fixed.
   It blocks every admin task in both files.
2. `B8.1` (open redirect) and `B8.2` (GET logout closes the guard's shift) — live
   security holes, both small.
3. `B8.5` + `B7.2` — `ALLOWED_HOSTS` has no `testserver`, so Django's test client gets
   HTTP 400 on *every* request. Until this is fixed you cannot write a single
   integration test.
4. `B7.1` — `seed_demo`. **The database is empty (0 rows, no superuser).** Frontend
   work is blind without it.
5. `B1.*` — role separation. Every view is `@role_required("GUARD")` (18 occurrences,
   not the 20 the task file claims — `grep -rn 'role_required' --include=*.py .`);
   there is no ADMIN-gated view anywhere. Everything else assumes this is done.

Then follow each file's own build order.

**Frontend depends on backend** for: Section 3 (POS) needs the JSON endpoints from
backend §3.4/§4.1/§4.2; Section 5 (scheduling UI) needs the backend `scheduling` app.
If the backend half isn't merged yet, either do the backend half first or build
against a documented stub and say so in the PR.

### Tasks that are the same work in both files — do once

| Backend | Frontend | Who does it |
|---|---|---|
| `B8.13` pin Lucide | `F7.5` pin Lucide | whoever gets there first; note it in the PR |
| `B8.11` Paginator | `F7.6` pagination UI | backend first, then frontend |
| `B8.1` validate `next` | `F7.1` hidden `next` input | same PR — they are one fix |
| `B4.3`/`B4.4`/`B8.12` overdue | `F7.8` overdue styling | backend first (nothing sets OVERDUE today) |
| `B2.5` guard post | `B8.9` hardcoded `"Main Desk"` | one change, `transactions/signals.py` |

---

## 4. Backend conventions

### Views: function-based, decorator-gated

Every view in this repo is a function view with a role decorator. Match that.
`accounts/mixins.py:RoleRequiredMixin` exists but is **used nowhere** (`B8.10`) — don't
start a second parallel mechanism; either use it consistently or delete it.

```python
# accounts/permissions.py  (TASK B1.1 — create this)
from .decorators import role_required

admin_required = role_required("ADMIN")
guard_required = role_required("GUARD")
admin_or_guard_required = role_required("ADMIN", "GUARD")
```

`role_required` already stacks `@login_required` and bypasses for superusers — keep
that behavior.

The existing shape to copy (`keys/views.py:25`):

```python
@admin_required
def key_create(request):
    form = KeyForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        obj.created_by = request.user
        obj.save()
        messages.success(request, f"Key {obj.key_code} added.")
        return redirect("keys:key_list")
    return render(request, "keys/key_form.html", {"form": form, "title": "Add key"})
```

**Every mutating view gets `@require_POST`.** Decorator order matters — role check
outermost, matching `transactions/views.py:99`:

```python
@guard_required
@require_POST
def borrow_confirm(request): ...
```

Never use `if request.method == "POST":` as the only guard on a toggle — that's the
`B8.3` defect; it silently redirects on GET instead of rejecting.

### URLs: always namespaced, always `reverse()`

Every app has `app_name` in its `urls.py`. **Never hardcode a path** — that's `B8.4`:

```python
# wrong (transactions/views.py:114 today)
return redirect(f"/transactions/borrow/?borrower_id={borrower_id}")

# right
from django.urls import reverse
from urllib.parse import urlencode
url = reverse("transactions:borrow_dashboard")
return redirect(f"{url}?{urlencode({'borrower_id': borrower_id})}")
```

### Forms: the `TAILWIND` constant pattern

All forms currently live in `accounts/forms.py` — even `KeyForm`, `BuildingForm` and
`CategoryForm`. That's odd, but it's the existing convention, and the forms the tasks
assign there (`RoomForm` per `B3.1`, `UserCreateForm` per `B2.1`) go there too. Forms
for the new `scheduling` app go in `scheduling/forms.py`.

Copy this `__init__` verbatim — it is identical in all four existing forms:

```python
TAILWIND = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-[14px] focus:border-[#1E6B4A] focus:ring-[#1E6B4A]"

def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    for name, f in self.fields.items():
        if isinstance(f.widget, forms.CheckboxInput):
            f.widget.attrs.setdefault("class", "h-4 w-4 accent-[#1E6B4A]")
        else:
            f.widget.attrs.setdefault("class", TAILWIND)
```

Surface DB constraint violations as a `ValidationError` in `clean()`, never as a 500.
Two constraints exist today: `unique_room_per_building` (Room) and
`unique_key_copy_per_room_category` (Key).

### Services: where the rules live

`transactions/services.py` is the model to follow:

```python
class BorrowError(Exception): pass          # one domain exception per app

@db_transaction.atomic
def borrow_key(*, key_id, borrower, guard, purpose="", request=None):
    ...
    raise BorrowError("You must start a shift before releasing keys.")
```

Keyword-only arguments (`*,`), `@db_transaction.atomic` on anything multi-write,
`select_for_update()` on rows two guards could race for. New services
(`scheduling/services.py` per `B5.3`) follow the same shape with their own
`ScheduleConflictError`.

### JSON endpoints (backend §3.4, §4.1, §4.2, §5.6)

Agreed contract — the frontend list depends on these exactly:

- `JsonResponse`, `select_related` to kill N+1.
- **403, not 302**, for wrong role on an AJAX call. `role_required` raises
  `PermissionDenied`, which renders 403, so that already works — just don't let an
  API path fall through to `login_required`'s redirect.
- **422** for a business-rule violation, body `{"error": "<message>"}` or
  `{"<field>": ["<errors>"]}`.
- **201** on create, **200** on update.
- CSRF stays on. The frontend sends `X-CSRFToken`.
- Never serialize password hashes or user emails.

```python
@guard_required
def borrower_search(request):
    q = request.GET.get("q", "").strip()
    qs = Borrower.objects.select_related("department").filter(is_active=True)
    if q:
        qs = qs.filter(Q(first_name__icontains=q) | Q(last_name__icontains=q)
                       | Q(id_number__icontains=q))
    return JsonResponse({"results": [...]})  # limit 20
```

### Migrations

`audit` and `notifications` have **no migrations at all** (`migrations/__init__.py`
only) because their `models.py` are stubs. After writing `audit/models.py` (`B6.1`):

```powershell
python manage.py makemigrations audit
python manage.py migrate
```

Commit the migration file. Never edit an applied migration — add a new one.
For `scheduling` (`B5.1`): `python manage.py startapp scheduling`, add it to
`INSTALLED_APPS`, then makemigrations.

### The conflict algorithm (`B5.3`) — get the boundary right

This is the core deliverable of backend §5, and the one place an off-by-one will be
caught in review:

```python
# two blocks conflict when they OVERLAP, not when they TOUCH
existing.start_time < new.end_time  AND  existing.end_time > new.start_time
```

09:00–10:00 and 10:00–11:00 are **both valid**. One DB query, not a Python loop.
Honor `exclude_block_id` so editing a block doesn't conflict with itself.
`B5.8` lists ten mandatory test cases — write all ten.

### Tests

Every `tests.py` is currently empty. `B8.5`/`B7.2` must land first or everything 400s.

```python
from django.test import TestCase, override_settings

@override_settings(ALLOWED_HOSTS=["testserver"])
class BorrowFlowTests(TestCase):
    ...
```

Run `python manage.py test`, or `python manage.py test scheduling` for one app.
**Rule: every rule you add to a `services.py` gets a test in the same PR.**

---

## 5. Frontend conventions

### Design tokens — fixed, do not invent

```
#0F2E1F  dark green — sidebar, primary
#143d28  primary hover
#14342A  button primary
#1E6B4A  accent / focus ring
#FAF7F0  page background
stone-800 body · stone-500/400 muted
card       bg-white rounded-xl  border border-stone-200 shadow-sm
modal card bg-white rounded-2xl border border-stone-200 shadow-xl
overlay    bg-stone-900/60
text       [13px] body · [15px] card title · [22px] page heading
```

Tailwind arbitrary values (`text-[13px]`, `bg-[#0F2E1F]`) are the house style — use
them rather than approximating with scale classes.

### Template structure

```django
{% extends "base.html" %}
{% block title %}Page name — KEYTRACK{% endblock %}
{% block content %}...{% endblock %}
{% block extra_js %}<script src="{% static 'js/pos.js' %}"></script>{% endblock %}
```

`base.html` gates on `user.is_authenticated`: authenticated gets sidebar + navbar +
`{% block content %}`; anonymous gets `{% block content_anon %}` (the login page uses
this). `{% block extra_head %}` and `{% block extra_js %}` already exist — use them;
stop putting `<script>` inline (`F6.9`).

New shared partials go in `templates/partials/`, per-app templates in
`templates/<app>/`. Modal form partials take a leading underscore:
`templates/facilities/_room_form_modal.html`.

### The modal system (`F1.*`) — generalize, don't copy

`templates/partials/logout_modal.html` is the **reference implementation**: overlay
click to close, Escape, focus restore, body scroll lock. It is a per-id IIFE bound to
`"logout-modal"`. Copying that nine times is the `F7.10` defect.

Build `static/js/modal.js` **once**, with delegated listeners:

- open: `[data-modal-open="<id>"]`
- close: `[data-modal-close]`, overlay click, Escape
- focus trap; focus first input on open; restore focus to the trigger on close
- `document.body.classList.add("overflow-hidden")` while open
- stacked modals via a z-index counter
- expose `window.KeyTrackModal = { open(id), close(id) }`

Then reduce `logout_modal.html` to markup only, keeping the behavior it already gets
right. Preserve its a11y attributes: `role="dialog"`, `aria-modal="true"`,
`aria-labelledby`.

Use `templates/partials/form_field.html` for modal form controls. Pass a Django
`BoundField` as `field` when available; simple modal controls can pass their label,
ID, name, type, required state, help text, and error details. This keeps required
markers and inline validation styling consistent.

### After **any** dynamic DOM injection, call `lucide.createIcons()`

Icons render as blank space otherwise. This bites on every AJAX modal, every POS search
result, every schedule grid repaint. It is the most common frontend bug in this stack.

### AJAX conventions

```js
// static/js/csrf.js
function getCsrfToken() {
  return document.cookie.split("; ").find(c => c.startsWith("csrftoken="))?.split("=")[1];
}

const res = await fetch(url, {
  method: "POST",
  headers: { "X-CSRFToken": getCsrfToken(), "Content-Type": "application/json" },
  body: JSON.stringify(payload),
});
```

Handle the agreed statuses: **200/201** success → close modal, toast, refresh region;
**422** → re-render the form in place with inline field errors, **modal stays open,
typed input preserved**; **403** → "You do not have permission" and close.

Debounce every live search **250–300ms**. Disable the submit button and show a spinner
while a request is in flight — a double-submit on the POS release button would
double-issue a key.

### Progressive enhancement

Keep the existing full-page form templates (`key_form.html`, `building_form.html`,
`borrower_form.html`) working as a no-JS fallback. Modal endpoints answer
`GET ?modal=1` with just the form partial; a plain GET still returns the full page.

### Role-aware UI

```django
{% if user.role == "ADMIN" %}
  <button data-modal-open="room-form">+ Add room</button>
{% endif %}
```

Hide admin controls in guard templates — **and** gate the view. Use
`{{ user.get_role_display }}` for the role label, never `{{ user.role|default:"GUARD" }}`
(that's `F7.2`: it prints "GUARD" for an admin, in a system whose entire point is role
separation).

Beware Django's `{% if %}` precedence (`F7.3`): `and` binds tighter than `or`, so
`ns == 'keys' and current == 'key_list' or current == 'key_create'` does **not** mean
what it looks like. Cleanest fix: compute one `active_nav` string in a context
processor and compare that.

### Note on `F7.9` (no mobile nav)

Partly stale: `templates/partials/navbar.html:17-24` *does* have a horizontal mobile
link strip. The real gap is that the sidebar is `hidden md:flex` with no drawer, so the
full role-aware nav is unreachable below `md`. Build the `F2.2` drawer, then either
remove the strip or keep it deliberately — don't ship both without deciding.

---

## 6. Verifying your work

Do all of these before opening a PR. Report real output; if something fails, say so.

```powershell
python manage.py check                              # no system check errors
python manage.py makemigrations --check --dry-run   # no uncommitted model changes
python manage.py test                               # needs B8.5 (testserver) merged
python manage.py tailwind runserver                 # then click the actual screens
git status                                          # no db.sqlite3 / .env / venv staged
```

Then, by hand:

- Log in as **both** an ADMIN and a GUARD. Confirm the guard cannot reach an admin URL
  **by typing it directly** — hidden links prove nothing.
- A guard with no open shift must be blocked from releasing a key, with a clear message,
  *before* filling the form (`F3.8`).
- POS screen at **1366×768** (the target lab monitor).
- Keyboard-only pass on any modal you touched: Tab stays inside, Escape closes, focus
  returns to the trigger.
- Watch for blank icons — that means a missing `lucide.createIcons()`.

---

## 7. Git workflow — what "can be pushed" means here

`main` is protected. Merge only via PR.

```powershell
git checkout main
git pull
git checkout -b feat/b1-role-separation     # or fix/b8-1-open-redirect
# ... work ...
git add <specific files>                    # never `git add -A` blindly
git status                                  # leak check
git commit
git push -u origin feat/b1-role-separation
gh pr create --base main
```

**Branch names:** `fix/b8-1-open-redirect`, `feat/b5-scheduling-conflicts`,
`feat/f1-modal-system`, `feat/f4-2-room-crud` — prefix, task id, slug.

**Scope:** one task, or one coherent group of related task ids (e.g. all of `B1.*`).
Never mix a backend section and a frontend section in one PR. Never sneak an unrelated
drive-by fix into a security PR.

**Commit message:**

```
B8.1 Validate login redirect target

accounts/views.py took `next` straight from the query string and passed it
to redirect(), so /accounts/login/?next=https://evil.example.com bounced a
freshly authenticated user off-site.

Gate it through url_has_allowed_host_and_scheme and read `next` from POST too
(login.html had no hidden input, so the target was lost on POST anyway — F7.1).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Subject: task id plus an imperative summary, 72 chars or less. Body: what was wrong,
what changed, why. Reference the task id so the backlog stays traceable.

**PR description must state:** which task ids are closed; what you verified and how
(paste real command output); anything you deliberately left out and why; any contract
the other half of the team must match (JSON shapes, context variables, template blocks).

End PR descriptions with:

```
🤖 Generated with [Claude Code](https://claude.com/claude-code)
```

**Do not commit or push unless the user asks.** Prepare the branch and the message;
let them pull the trigger.

---

## 8. When the task file is wrong

The task files were written from code review and are mostly accurate, but they are not
scripture. Confirmed discrepancies:

- **Django version**: the files say 5.x; `requirements.txt` pins **6.1.2**.
- **`B7.4`/`B8.15`**: "move SECRET_KEY and DEBUG to .env" is **already done**
  (`config/settings.py:22,25`). `B8.15` says so. Skip that bullet; the rest of `B7.4`
  still stands.
- **`F7.9`**: a mobile link strip does exist in `navbar.html` — see §5.
- **`B1.2`** says "20 views across 4 apps"; the actual count is 18
  `@role_required("GUARD")` decorators. Re-grep rather than trusting the number, and
  re-gate whatever is actually there.
- **`B7.1`** says the DB is empty — verify with `python manage.py shell` before
  assuming; someone may have seeded it since.

If a task describes code that doesn't match what you read: **trust the code, say so in
the PR, and do the right thing.** Don't implement a fix for a bug that isn't there, and
don't skip a task because a line number drifted — find the real code.

If a task is genuinely blocked (needs the other half of the team, or a decision only
the user can make — `B8.6` "delete home.html or route it?" is one), do every unblocked
part, then state the block explicitly. Don't guess at a product decision, and don't
quietly shrink the task either.
