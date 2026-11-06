from decimal import Decimal
from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models

class Migration(migrations.Migration):
    dependencies=[("lab","0018_preparation_calculator"), migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[
        migrations.CreateModel(name='PreparationDraftSelection', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('preparation', models.OneToOneField('lab.preparation', on_delete=models.PROTECT, related_name='draft_selection', verbose_name='Черновик приготовления')),
            ('protocol', models.ForeignKey('lab.preparationprotocol', on_delete=models.PROTECT, related_name='draft_selections', verbose_name='Исходный протокол')),
            ('request_key', models.UUIDField('Ключ создания', unique=True, editable=False)),
            ('planned_volume_ml', models.DecimalField('Планируемый объём, мл', max_digits=16, decimal_places=4, validators=[MinValueValidator(Decimal('0.0001'))])),
            ('source_digest', models.CharField('Отпечаток подбора', max_length=64)),
            ('request_digest', models.CharField('Отпечаток создания', max_length=64)),
            ('snapshot', models.JSONField('Снимок плана', default=dict)),
            ('created_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='created_preparation_drafts', verbose_name='Создал')),
            ('created_at', models.DateTimeField('Когда создан', auto_now_add=True)),
            ('facts_confirmed_by', models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='confirmed_preparation_facts', verbose_name='Подтвердил факт', null=True, blank=True)),
            ('facts_confirmed_at', models.DateTimeField('Когда подтверждён факт', null=True, blank=True)),
            ('facts_digest', models.CharField('Отпечаток факта', max_length=64, blank=True, default='')),
            ('facts_snapshot', models.JSONField('Подтверждённые фактические данные', default=dict, blank=True))
        ], options={'verbose_name': 'Сохранённый подбор приготовления', 'verbose_name_plural': 'Сохранённые подборы приготовлений', 'constraints': [models.CheckConstraint(condition=models.Q(planned_volume_ml__gt=0), name='draft_volume_positive_7v'), models.CheckConstraint(condition=models.Q(facts_confirmed_by__isnull=True, facts_confirmed_at__isnull=True, facts_digest='') | models.Q(facts_confirmed_by__isnull=False, facts_confirmed_at__isnull=False) & ~models.Q(facts_digest=''), name='draft_fact_confirmation_7v')]}),
        migrations.CreateModel(name='PreparationDraftLine', fields=[
            ('id', models.BigAutoField(primary_key=True, serialize=False)),
            ('selection', models.ForeignKey('lab.preparationdraftselection', on_delete=models.PROTECT, related_name='lines', verbose_name='Подбор')),
            ('recipe_component', models.ForeignKey('lab.preparationprotocolcomponent', on_delete=models.PROTECT, related_name='draft_lines', verbose_name='Компонент рецептуры')),
            ('lot', models.ForeignKey('lab.lot', on_delete=models.PROTECT, related_name='preparation_draft_lines', verbose_name='Выбранная партия')),
            ('planned_qty', models.DecimalField('План из партии', max_digits=16, decimal_places=4, null=True, blank=True, validators=[MinValueValidator(Decimal('0.0001'))])),
            ('unit_snapshot', models.CharField('Складская единица', max_length=20)),
            ('manual_amount', models.BooleanField('Количество определяется фактически', default=False))
        ], options={'verbose_name': 'Плановая строка приготовления', 'verbose_name_plural': 'Плановые строки приготовления', 'constraints': [models.UniqueConstraint(fields=['selection', 'recipe_component', 'lot'], name='draft_component_lot_unique_7v'), models.CheckConstraint(condition=models.Q(planned_qty__isnull=False, planned_qty__gt=0) | models.Q(planned_qty__isnull=True, manual_amount=True), name='draft_line_quantity_7v')]})
    ]
