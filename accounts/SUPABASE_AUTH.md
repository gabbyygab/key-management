# Supabase Auth for guards and admins

Supabase Auth checks the password. Django still owns the session, the role
(`ADMIN` / `GUARD`), approval and `is_active`, so `@admin_required`,
`@guard_required` and the shift-on-login signal work the same as before.

```
login form ──► SupabaseAuthBackend ──► find Django user (username or email)
                     │                    └─ inactive / not approved → refused, Supabase never called
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
3. In the Supabase dashboard, **Authentication → Sign In / Providers**: turn
   **off** "Allow new users to sign up". Only KEYTRACK admins should create
   accounts. The backend refuses unlinked Supabase users anyway, but there is no
   reason to let strangers into `auth.users`.

## Existing accounts

No bulk import is needed (Django cannot export passwords). Each existing user is
moved on their first login: the Django password is checked, a Supabase user is
created with it, and `supabase_uid` is stored. If a Supabase user with that email
already exists it is re-keyed to the verified password, which locks out anyone
who registered the email first.

## What the admin screens do

| Screen | Django | Supabase |
|---|---|---|
| Add user | creates user (pre-approved) | creates confirmed auth user; on failure the Django user is rolled back |
| Edit user | saves | updates email + `user_metadata` (role, username) |
| Reset password | sets hash | sets password; rolled back together on failure |
| Deactivate / activate | `is_active` | ban / lift ban (warning only if Supabase fails; Django's flag is what login checks) |
| Approve / reject | approval status | nothing; approval is checked in Django |

## Offline fallback

`SUPABASE_AUTH_OFFLINE_FALLBACK=True` (default) keeps the Django hash equal to the
Supabase password. If Supabase cannot be reached, the local hash answers so the
guard desk keeps working; wrong passwords still count toward the login lockout.
Set it to `False` to require Supabase for every login.

A password changed directly in the Supabase dashboard reaches the Django hash on
that user's next online login.
