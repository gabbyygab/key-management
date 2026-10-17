# Build Log — 2026-10-16 — First Push to GitHub

Repo: https://github.com/Robb730/key-management (public, PR workflow).

## Prep before first commit
- Installed `python-decouple==3.8` (was missing) and switched
  `config/settings.py` to env vars: `SECRET_KEY = config("SECRET_KEY")`,
  `DEBUG` (default False), `ALLOWED_HOSTS` (Csv, default 127.0.0.1,localhost).
  Old hardcoded `django-insecure-...` key removed — verified absent from history via `git grep`.
- Created local `.env` with a fresh generated secret; `.env.example` committed as template.
- `.gitignore`: `venv/`, `__pycache__/`, `*.pyc`, `db.sqlite3`, `.env`, `media/`,
  `staticfiles/`, `.vscode/`.
- `requirements.txt` regenerated via `pip freeze`; `README.md` with setup + workflow.

## Commit + push
- `git init -b main`, staged 95 files. Leak check: no `venv/`, `db.sqlite3`,
  `.env`, `media/`, `staticfiles/`, or `__pycache__` staged.
- Commit `3ac8eba` "Initial commit: KEYTRACK MVP (borrow management, auto shifts, KEYTRACK theme)".
- `git remote add origin https://github.com/Robb730/key-management.git`,
  `git push -u origin main` — success, working tree clean.

## Still to do on GitHub (web UI)
- Settings → Branches → protect `main` (require pull request before merging).
- Settings → Collaborators → invite people; outsiders use fork → PR.
- Collaborators onboard via README: clone → venv → `pip install -r requirements.txt` →
  copy `.env.example` → `.env` with own `SECRET_KEY` → `migrate` → `createsuperuser`.
