# Supabase Auth for guards and admins

Supabase Auth checks the password. Django still owns the session, the role
(`ADMIN` / `GUARD`) and `is_active`, so `@admin_required`,
`@guard_required` and the shift-on-login signal work the same as before.

```
login form ──► SupabaseAuthBackend ──► find Django user (username or email)
                     │                    └─ inactive → refused, Supabase never called
                     ├─► Supabase /auth/v1/token (email + password, anon key)
                     │      ok        → uid must equal user.supabase_uid → Django session
                     │      wrong pw  → refused (or first-login move, below)
                     │      down      → local hash if OFFLINE_FALLBACK, else "service unreachable"
admin screens ──► push_password / push_profile / push_active (service-role key)
```

## Turning it on

1. `.env`:
   ```
   SUPABASE_URL=https://<ref>.supabase.co
   SUPABASE_ANON_KEY=sb_publishable_...      # or the legacy anon JWT
   SUPABASE_SERVICE_ROLE_KEY=...             # server side only
   SUPABASE_AUTH_ENABLED=True
   ```
2. `python manage.py migrate` (adds `accounts_user.supabase_uid`).
3. **Only admins add accounts.** Run `supabase/only_keytrack_creates_users.sql`
   once in the Supabase SQL editor. It refuses any `auth.users` row that lacks
   `app_metadata.keytrack_provisioned`, a flag only the service-role key can set,
   so public sign-up, OAuth, magic links and the dashboard's "Add user" all fail.
   Accounts are made only from KEYTRACK's **Users → Add user** (admin role).
   Also turn **off** "Allow new users to sign up" under
   **Authentication → Sign In / Providers**, so sign-up attempts get a clean
   refusal instead of a database error.

   In Django, the Add user screen and the `/admin/` user add page both require
   the ADMIN role (a staff flag alone is not enough).

## Existing accounts

No bulk import is needed (Django cannot export passwords). Each existing user is
moved on their first login: the Django password is checked, a Supabase user is
created with it, and `supabase_uid` is stored. If a Supabase user with that email
already exists it is re-keyed to the verified password, which locks out anyone
who registered the email first.

## What the admin screens do

| Screen | Django | Supabase |
|---|---|---|
| Add user | creates user with `DEFAULT_USER_PASSWORD` (`Admin2026`) | creates confirmed auth user with the same password; on failure the Django user is rolled back |
| Change password (navbar key icon, the user themselves) | checks the current password through Supabase, sets the new hash | sets password; rolled back together on failure. Wrong current passwords share the login lockout |
| Edit user | saves (refused while the account is on duty) | updates email + `user_metadata` (role, username) |
| Reset password | sets hash | sets password; rolled back together on failure |
| Deactivate / activate | `is_active` | ban / lift ban (warning only if Supabase fails; Django's flag is what login checks) |
| Delete user | deletes the row (refused while on duty, for your own account, and when a shift or transaction references it) | deletes the auth user; a failure is a warning only, since the Django row is already gone |

## Offline fallback

`SUPABASE_AUTH_OFFLINE_FALLBACK=True` (default) keeps the Django hash equal to the
Supabase password. If Supabase cannot be reached, the local hash answers so the
guard desk keeps working; wrong passwords still count toward the login lockout.
Set it to `False` to require Supabase for every login.

A password changed directly in the Supabase dashboard reaches the Django hash on
that user's next online login.
