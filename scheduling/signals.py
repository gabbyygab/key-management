"""A new term inherits the previous term's curriculum."""
from django.db.models.signals import post_save
from django.dispatch import receiver

from .curriculum import seed_from_previous
from .models import CurriculumSubject, Term


@receiver(post_save, sender=Term)
def term_saved(sender, instance, created, raw=False, **kwargs):
    if raw:
        return
    if created:
        seed_from_previous(instance)
    else:
        # The semester was edited: keep each row's copy of it in step.
        CurriculumSubject.objects.filter(term=instance).exclude(
            semester=instance.semester).update(semester=instance.semester)
