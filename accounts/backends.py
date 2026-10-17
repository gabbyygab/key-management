from django.contrib.auth.backends import ModelBackend


class ApprovedUserBackend(ModelBackend):
    def user_can_authenticate(self, user):
        # Superusers always allowed (so initial setup never locks out).
        if getattr(user, "is_superuser", False):
            return super().user_can_authenticate(user)
        return super().user_can_authenticate(user) and user.is_approved
