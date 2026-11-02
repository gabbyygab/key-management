import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend

from .supabase_auth import InvalidCredentials, SupabaseAuthError, SupabaseAuthUnavailable, get_client

log = logging.getLogger(__name__)


class ApprovedUserBackend(ModelBackend):
    def user_can_authenticate(self, user):
        # Superusers always allowed (so initial setup never locks out).
        if getattr(user, "is_superuser", False):
            return super().user_can_authenticate(user)
        return super().user_can_authenticate(user) and user.is_approved


class SupabaseAuthBackend(ApprovedUserBackend):
    """Supabase Auth checks the password; Django decides who may come in.

    The login form accepts a username or an email. The Django account is found
    first, then its email and the typed password go to Supabase. Role,
    approval and is_active are still enforced here and by the role decorators.

    Accounts made before the switch have no ``supabase_uid``. On their first
    login the old Django password is checked, and if it matches, a Supabase
    user is created (or the existing one re-keyed) with that password and
    linked. A Supabase user is never linked by email alone: with public
    sign-ups on, anyone could register a guard's email in Supabase.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        if not username or not password:
            return None
        user = self._find(username)
        if user is None:
            # Same hashing cost as a real attempt, so timing does not reveal
            # which usernames exist.
            get_user_model()().set_password(password)
            return None
        if not self.user_can_authenticate(user) or not user.email:
            return None

        try:
            sb_user = get_client().sign_in(user.email, password)
        except InvalidCredentials:
            if user.supabase_uid is None and user.check_password(password):
                return self._migrate(user, password)
            return None
        except SupabaseAuthUnavailable as e:
            log.warning("Supabase Auth unreachable for %s: %s", user.username, e)
            if settings.SUPABASE_AUTH_OFFLINE_FALLBACK:
                # The local hash answers instead, and a wrong password is an
                # ordinary failure that still counts toward the lockout.
                return user if user.check_password(password) else None
            if request is not None:
                request.supabase_auth_unavailable = True
            return None
        except SupabaseAuthError as e:
            log.error("Supabase Auth is misconfigured: %s", e)
            if request is not None:
                request.supabase_auth_unavailable = True
            return None

        uid = sb_user.get("id")
        if user.supabase_uid is None:
            # A Supabase user with this email already existed. Link it only if
            # the typed password also matches the Django one, proving this is
            # the account holder and not someone who signed up with the email.
            if not user.check_password(password):
                log.warning("Refused to link unlinked Supabase user %s to %s.", uid, user.username)
                return None
            user.supabase_uid = uid
            user.save(update_fields=["supabase_uid"])
        elif str(user.supabase_uid) != str(uid):
            log.warning("Supabase uid mismatch for %s.", user.username)
            return None

        self._sync_local_hash(user, password)
        return user

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _find(identifier):
        User = get_user_model()
        identifier = identifier.strip()
        user = User.objects.filter(username=identifier).first()
        if user is None and "@" in identifier:
            user = User.objects.filter(email__iexact=identifier).first()
        return user

    @staticmethod
    def _migrate(user, password):
        from .services import push_password  # avoid import cycle at load
        try:
            push_password(user, password)
        except SupabaseAuthError as e:
            # The Django password was right, so the person is who they say.
            # Let them in; the next login retries the move.
            log.error("Could not move %s to Supabase Auth: %s", user.username, e)
        return user

    @staticmethod
    def _sync_local_hash(user, password):
        """Keep the Django hash equal to the Supabase password, for the offline
        fallback. Covers a password changed in the Supabase dashboard."""
        if not settings.SUPABASE_AUTH_OFFLINE_FALLBACK:
            return
        if not user.check_password(password):
            user.set_password(password)
            user.save(update_fields=["password"])
