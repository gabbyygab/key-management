from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    name = 'scheduling'

    def ready(self):
        from . import signals  # noqa: F401
