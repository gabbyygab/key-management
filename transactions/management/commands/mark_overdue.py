from django.core.management.base import BaseCommand

from transactions.services import mark_overdue


class Command(BaseCommand):
    help = ("Mark ACTIVE transactions past their expected return time as OVERDUE "
            "(and their keys). Safe to run repeatedly, e.g. from cron every 5 minutes.")

    def handle(self, *args, **options):
        count = mark_overdue()
        self.stdout.write(self.style.SUCCESS(f"Marked {count} transaction(s) overdue."))
