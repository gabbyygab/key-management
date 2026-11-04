import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """Step 1 of per-term curricula: a nullable term, old slot constraints gone."""

    dependencies = [('scheduling', '0010_curriculum')]

    operations = [
        migrations.RemoveConstraint(model_name='curriculumsubject', name='unique_curriculum_subject'),
        migrations.RemoveIndex(model_name='curriculumsubject', name='curriculum_slot_idx'),
        migrations.AddField(
            model_name='curriculumsubject', name='term',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='curriculum', to='scheduling.term'),
        ),
    ]
