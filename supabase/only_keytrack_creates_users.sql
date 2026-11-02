-- Only KEYTRACK admins create Supabase Auth users.
--
-- KEYTRACK creates users through the Auth admin API with the service-role key
-- and sets app_metadata.keytrack_provisioned = true (accounts/supabase_auth.py).
-- app_metadata cannot be set through the public /signup endpoint, so this
-- refuses every other way a row could appear in auth.users: public email
-- sign-up, OAuth, magic-link auto sign-up, and the dashboard's "Add user".
--
-- The check is a deferred constraint trigger: it runs at commit and re-reads
-- the row, so it holds even if Auth writes app_metadata in a second statement
-- of the same transaction.
--
-- Undo:  drop trigger if exists keytrack_only_provisioned_users on auth.users;
--        drop function if exists private.keytrack_only_provisioned_users();

create schema if not exists private;

create or replace function private.keytrack_only_provisioned_users()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if not exists (
    select 1 from auth.users u
    where u.id = new.id
      and coalesce((u.raw_app_meta_data ->> 'keytrack_provisioned')::boolean, false)
  ) then
    raise exception 'Accounts are created by KEYTRACK administrators only.'
      using errcode = '42501';
  end if;
  return null;
end;
$$;

revoke all on function private.keytrack_only_provisioned_users() from public, anon, authenticated;
grant usage on schema private to supabase_auth_admin;
grant execute on function private.keytrack_only_provisioned_users() to supabase_auth_admin;

drop trigger if exists keytrack_only_provisioned_users on auth.users;
create constraint trigger keytrack_only_provisioned_users
  after insert on auth.users
  deferrable initially deferred
  for each row execute function private.keytrack_only_provisioned_users();
