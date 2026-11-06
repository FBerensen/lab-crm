from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.core.validators

class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL), ("lab", "0020_sample_projects")]
    operations = [
        migrations.CreateModel(name='SourceBlock', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('name', models.CharField(max_length=160, verbose_name="Название блока")),
            ('default_dna_ul', models.DecimalField(decimal_places=4, max_digits=16, validators=[django.core.validators.MinValueValidator(Decimal("0"))], verbose_name="ДНК по умолчанию на лунку, мкл")),
            ('default_water_ul', models.DecimalField(decimal_places=4, max_digits=16, validators=[django.core.validators.MinValueValidator(Decimal("0"))], verbose_name="Вода по умолчанию на лунку, мкл")),
            ('note', models.TextField(blank=True, verbose_name="Примечание")),
            ('revision', models.PositiveBigIntegerField(default=0, editable=False, verbose_name="Версия схемы")),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='source_blocks_created_8v', to=settings.AUTH_USER_MODEL, verbose_name='Создал')),
            ('updated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='source_blocks_updated_8v', to=settings.AUTH_USER_MODEL, verbose_name='Последнее изменение')),
            ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создан')),
            ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменён')),
        ], options={'verbose_name': 'Исходный блок', 'verbose_name_plural': 'Исходные блоки', 'ordering': ('-created_at', '-pk')}),
        migrations.CreateModel(name='SourceBlockWell', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('block', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="wells", to="lab.sourceblock", verbose_name="Исходный блок")),
            ('row', models.CharField(choices=[(r,r) for r in "ABCDEFGH"], max_length=1, verbose_name="Ряд")),
            ('column', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(12)], verbose_name="Колонка")),
            ('sample', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="source_block_wells", to="lab.sample", verbose_name="Образец / проба")),
            ('dna_ul', models.DecimalField(decimal_places=4, max_digits=16, validators=[django.core.validators.MinValueValidator(Decimal("0"))], verbose_name="ДНК, мкл")),
            ('water_ul', models.DecimalField(decimal_places=4, max_digits=16, validators=[django.core.validators.MinValueValidator(Decimal("0"))], verbose_name="Вода, мкл")),
            ('note', models.TextField(blank=True, verbose_name="Примечание к лунке")),
            ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='source_wells_created_8v', to=settings.AUTH_USER_MODEL, verbose_name='Создал')),
            ('updated_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='source_wells_updated_8v', to=settings.AUTH_USER_MODEL, verbose_name='Последнее изменение')),
            ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создана')),
            ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменена')),
        ], options={'verbose_name': 'Лунка исходного блока', 'verbose_name_plural': 'Лунки исходных блоков', 'ordering': ('row', 'column')}),
        migrations.AddConstraint(model_name="sourceblock", constraint=models.CheckConstraint(condition=models.Q(default_dna_ul__gte=0, default_water_ul__gte=0), name="sb8v_defaults_nonnegative")),
        migrations.AddConstraint(model_name="sourceblockwell", constraint=models.UniqueConstraint(fields=["block","row","column"], name="sb8v_unique_position")),
        migrations.AddConstraint(model_name="sourceblockwell", constraint=models.CheckConstraint(condition=models.Q(row__in=list("ABCDEFGH"), column__gte=1, column__lte=12), name="sb8v_position_valid")),
        migrations.AddConstraint(model_name="sourceblockwell", constraint=models.CheckConstraint(condition=models.Q(dna_ul__gte=0, water_ul__gte=0), name="sb8v_volumes_nonnegative")),
    ]
