from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies = [("lab", "0016_stock_availability"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations = [
        migrations.CreateModel(name='LaboratoryProtocol', fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('code', models.CharField('Код методики', max_length=80)),
                ('title', models.CharField('Название', max_length=240)),
                ('version', models.CharField('Версия', max_length=40, default='1')),
                ('method_kind', models.CharField('Вид методики', max_length=20, choices=[('dna', 'Выделение ДНК'), ('pcr', 'ПЦР'), ('other', 'Другое')], default='other')),
                ('instructions', models.TextField('Инструкция')),
                ('active', models.BooleanField('Доступна для новых выполнений', default=True)),
                ('created_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_lab_protocols', verbose_name='Создал')),
                ('created_at', models.DateTimeField('Когда создана', auto_now_add=True))
            ], options={'verbose_name': 'Лабораторная методика', 'verbose_name_plural': 'Лабораторные методики', 'ordering': ('title', 'version', 'pk'), 'constraints': [models.UniqueConstraint(fields=['code', 'version'], name='lab_protocol_code_version_6b2')]}),
        migrations.CreateModel(name='LaboratoryProtocolRun', fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('task', models.ForeignKey('lab.task', on_delete=models.PROTECT, related_name='laboratory_runs', verbose_name='Задача')),
                ('protocol', models.ForeignKey('lab.laboratoryprotocol', on_delete=models.PROTECT, related_name='runs', verbose_name='Методика и версия')),
                ('executor', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='laboratory_runs', verbose_name='Исполнитель')),
                ('status', models.CharField('Состояние', max_length=16, choices=[('draft', 'Черновик'), ('running', 'В работе'), ('closed', 'Закрыто')], default='draft')),
                ('started_on', models.DateField('Дата начала', null=True, blank=True)),
                ('finished_on', models.DateField('Дата завершения', null=True, blank=True)),
                ('result', models.TextField('Результат', blank=True)),
                ('note', models.TextField('Примечание', blank=True)),
                ('protocol_snapshot', models.JSONField('Снимок методики', default=dict, blank=True)),
                ('created_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_laboratory_runs', verbose_name='Создал')),
                ('created_at', models.DateTimeField('Когда создано', auto_now_add=True)),
                ('closed_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='closed_laboratory_runs', verbose_name='Закрыл', null=True, blank=True)),
                ('closed_at', models.DateTimeField('Когда закрыто', null=True, blank=True))
            ], options={'verbose_name': 'Выполнение лабораторной методики', 'verbose_name_plural': 'Выполнения лабораторных методик', 'constraints': [models.CheckConstraint(condition=models.Q(status='draft', started_on__isnull=True, finished_on__isnull=True, closed_by__isnull=True, closed_at__isnull=True) | models.Q(status='running', started_on__isnull=False, finished_on__isnull=True, closed_by__isnull=True, closed_at__isnull=True) | models.Q(status='closed', started_on__isnull=False, finished_on__isnull=False, closed_by__isnull=False, closed_at__isnull=False), name='lab_run_state_6b2'), models.CheckConstraint(condition=models.Q(finished_on__isnull=True) | models.Q(finished_on__gte=models.F('started_on')), name='lab_run_date_order_6b2')]}),
        migrations.CreateModel(name='LaboratoryProtocolRunSample', fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('run', models.ForeignKey('lab.laboratoryprotocolrun', on_delete=models.PROTECT, related_name='sample_rows', verbose_name='Выполнение')),
                ('sample', models.ForeignKey('lab.sample', on_delete=models.PROTECT, related_name='laboratory_run_rows', verbose_name='Проба')),
                ('note', models.TextField('Примечание', blank=True))
            ], options={'verbose_name': 'Проба выполнения', 'verbose_name_plural': 'Пробы выполнения', 'constraints': [models.UniqueConstraint(fields=['run', 'sample'], name='lab_run_sample_unique_6b2')]}),
        migrations.CreateModel(name='TaskMaterialPlan', fields=[
                ('id', models.BigAutoField(primary_key=True, serialize=False)),
                ('task', models.ForeignKey('lab.task', on_delete=models.PROTECT, related_name='material_plans', verbose_name='Задача')),
                ('lot', models.ForeignKey('lab.lot', on_delete=models.PROTECT, related_name='task_material_plans', verbose_name='Складская позиция')),
                ('planned_qty', models.DecimalField('Общий план на задачу', max_digits=16, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])),
                ('unit_snapshot', models.CharField('Единица учёта', max_length=20)),
                ('active', models.BooleanField('Учитывать в плане', default=True)),
                ('note', models.TextField('Примечание', blank=True)),
                ('created_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_task_material_plans', verbose_name='Создал')),
                ('created_at', models.DateTimeField('Когда создано', auto_now_add=True)),
                ('updated_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='updated_task_material_plans', verbose_name='Изменил')),
                ('updated_at', models.DateTimeField('Когда изменено', auto_now=True))
            ], options={'verbose_name': 'План продукта задачи', 'verbose_name_plural': 'План продуктов задач', 'constraints': [models.UniqueConstraint(fields=['task', 'lot'], name='task_lot_plan_unique_6b2'), models.CheckConstraint(condition=models.Q(planned_qty__gt=0), name='task_lot_plan_positive_6b2')]}),
        migrations.AddField("laboratoryprotocolrun", "samples", models.ManyToManyField('lab.sample', through='lab.LaboratoryProtocolRunSample', related_name='laboratory_runs', verbose_name='Пробы', blank=True)),
        migrations.AddField("worklog", "laboratory_run", models.ForeignKey("lab.laboratoryprotocolrun", on_delete=models.PROTECT, related_name="worklogs", verbose_name="Выполнение методики", null=True, blank=True))
    ]
