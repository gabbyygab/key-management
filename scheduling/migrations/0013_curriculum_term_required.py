import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('scheduling', '0012_curriculum_per_term_data')]

    operations = [
        migrations.AlterField(
            model_name='curriculumsubject', name='term',
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT,
                                    related_name='curriculum', to='scheduling.term'),
        ),
        migrations.AlterModelOptions(
            name='curriculumsubject',
            options={'ordering': ['program', 'year_level', 'term', 'subject__code']},
        ),
        migrations.AddConstraint(
            model_name='curriculumsubject',
            constraint=models.UniqueConstraint(fields=('term', 'program', 'year_level', 'subject'),
                                               name='unique_curriculum_term_subject'),
        ),
        migrations.AddIndex(
            model_name='curriculumsubject',
            index=models.Index(fields=['term', 'program', 'year_level'], name='curriculum_term_slot_idx'),
        ),
    ]
