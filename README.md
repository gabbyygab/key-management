# KEYTRACK — University Key Management

Campus key management system: guards release and receive room keys, borrowers
(professors/staff/students) are searchable people, one active key per borrower,
shifts start on login and end on logout.

Stack: Python, Django, Tailwind CSS (via `django-tailwind-cli`), SQLite (dev).

## Quick start (collaborators)

```powershell
git clone https://github.com/Robb730/key-management.git
cd key-management
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# edit .env and set your own SECRET_KEY (generate one with the command below)
python -c "import secrets; print(secrets.token_urlsafe(50))"
python manage.py migrate
python manage.py createsuperuser
# then in /admin/: set the superuser role + approval_status, or create guard users
# (role=GUARD, approval_status=APPROVED, usable password)
python manage.py tailwind runserver
```

Open `http://127.0.0.1:8000/accounts/login/` and log in as a guard.
A shift starts automatically; logging out ends it.

## Notes

- Only **GUARD** accounts (plus superusers) can log into the dashboard.
  Other roles get a login error. Manage login accounts in `/admin/` → Users.
- **Borrowers** (Borrowers page) are searchable profiles, not logins.
- Each developer gets their own `db.sqlite3` (gitignored) — never commit it.
- Never commit `.env`. CI/production must provide `SECRET_KEY`, `DEBUG=False`,
  and real `ALLOWED_HOSTS`.

## Workflow

- Branch per change: `git checkout -b feat/xyz` → commit → push → open a pull
  request against `main`. `main` is protected: merge only via PR review.
