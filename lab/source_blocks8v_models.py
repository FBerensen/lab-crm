from decimal import Decimal
from uuid import uuid4
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models

ROWS = tuple("ABCDEFGH")

def volume(label, **kwargs):
    return models.DecimalField(label, max_digits=16, decimal_places=4,
        validators=[MinValueValidator(Decimal("0"))], **kwargs)

def person(related_name, label):
    return models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        related_name=related_name, verbose_name=label)

class SourceBlock(models.Model):
    id = models.BigAutoField(primary_key=True)
    storage_place = models.ForeignKey("lab.StoragePlace", on_delete=models.PROTECT, related_name="source_blocks", verbose_name="Место хранения", null=True, blank=True)
    name = models.CharField("Название блока", max_length=160)
    default_dna_ul = volume("ДНК по умолчанию на лунку, мкл")
    default_water_ul = volume("Вода по умолчанию на лунку, мкл")
    note = models.TextField("Примечание", blank=True)
    revision = models.PositiveBigIntegerField("Версия схемы", default=0, editable=False)
    created_by = person("source_blocks_created_8v", "Создал")
    updated_by = person("source_blocks_updated_8v", "Последнее изменение")
    created_at = models.DateTimeField("Создан", auto_now_add=True)
    updated_at = models.DateTimeField("Изменён", auto_now=True)
    @property
    def code(self):
        return f"ИБ-{self.pk:06d}" if self.pk else "После сохранения"
    def __str__(self):
        return f"{self.code} · {self.name}"
    class Meta:
        app_label = "lab"
        verbose_name = "Исходный блок"
        verbose_name_plural = "Исходные блоки"
        ordering = ("-created_at", "-pk")
        constraints = [models.CheckConstraint(condition=models.Q(default_dna_ul__gte=0, default_water_ul__gte=0),name="sb8v_defaults_nonnegative")]

class SourceBlockWell(models.Model):
    class Kind(models.TextChoices):
        SAMPLE = "sample", "Зарегистрированная проба"
        WATER = "water", "Водный контроль"
        RAW = "unregistered", "Не зарегистрировано"
    id = models.BigAutoField(primary_key=True)
    block = models.ForeignKey(SourceBlock, on_delete=models.PROTECT,related_name="wells", verbose_name="Исходный блок")
    row = models.CharField("Ряд", max_length=1, choices=[(r,r) for r in ROWS])
    column = models.PositiveSmallIntegerField("Колонка",validators=[MinValueValidator(1), MaxValueValidator(12)])
    kind = models.CharField("Тип позиции",max_length=12,choices=Kind.choices,default=Kind.SAMPLE)
    raw_label = models.CharField("Незарегистрированное обозначение",max_length=500,blank=True,default="")
    sample = models.ForeignKey("lab.Sample", on_delete=models.PROTECT,related_name="source_block_wells", verbose_name="Образец / проба",null=True,blank=True)
    dna_ul = volume("ДНК, мкл")
    water_ul = volume("Вода, мкл",null=True,blank=True)
    note = models.TextField("Примечание к лунке", blank=True)
    created_by = person("source_wells_created_8v", "Создал")
    updated_by = person("source_wells_updated_8v", "Последнее изменение")
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    updated_at = models.DateTimeField("Изменена", auto_now=True)
    @property
    def position(self):return f"{self.row}{self.column}"
    @property
    def display_label(self):
        return self.sample.code if self.kind==self.Kind.SAMPLE and self.sample_id else ("вода" if self.kind==self.Kind.WATER else self.raw_label)
    def clean(self):
        super().clean()
        if self.kind==self.Kind.SAMPLE:
            if not self.sample_id or self.raw_label or self.water_ul is None:
                raise ValidationError("Для зарегистрированной пробы нужны карточка и оба объёма; свободная подпись должна быть пустой.")
        elif self.kind==self.Kind.WATER:
            if self.sample_id or self.raw_label or self.dna_ul!=0:
                raise ValidationError("Водный контроль не связан с пробой и содержит 0 мкл ДНК.")
        elif self.kind==self.Kind.RAW:
            if self.sample_id or not self.raw_label.strip() or self.water_ul is None:
                raise ValidationError("Для незарегистрированной позиции нужны подпись и оба объёма, без ссылки на пробу.")
    def __str__(self):return f"{self.block.code} / {self.position} / {self.display_label}"
    class Meta:
        app_label="lab"
        verbose_name="Лунка исходного блока"
        verbose_name_plural="Лунки исходных блоков"
        ordering=("row","column")
        constraints=[
            models.UniqueConstraint(fields=["block","row","column"],name="sb8v_unique_position"),
            models.CheckConstraint(condition=models.Q(row__in=list(ROWS),column__gte=1,column__lte=12),name="sb8v_position_valid"),
            models.CheckConstraint(condition=models.Q(dna_ul__gte=0,water_ul__gte=0),name="sb8v_volumes_nonnegative"),
            models.CheckConstraint(condition=(
                models.Q(kind="sample",sample__isnull=False,raw_label="",water_ul__isnull=False)|
                models.Q(kind="water",sample__isnull=True,raw_label="",dna_ul=0)|
                models.Q(kind="unregistered",sample__isnull=True,raw_label__regex=r"\S",water_ul__isnull=False)),name="sb8g_kind_consistent"),
        ]

class SourceBlockImport(models.Model):
    id=models.UUIDField(primary_key=True,default=uuid4,editable=False)
    block=models.ForeignKey(SourceBlock,on_delete=models.PROTECT,related_name="excel_imports",verbose_name="Исходный блок")
    created_by=person("source_imports_created_8g","Подготовил импорт")
    created_at=models.DateTimeField(auto_now_add=True)
    expires_at=models.DateTimeField()
    expected_revision=models.PositiveBigIntegerField()
    file_digest=models.CharField(max_length=64)
    payload=models.JSONField()
    applied_at=models.DateTimeField(null=True,blank=True)
    result_revision=models.PositiveBigIntegerField(null=True,blank=True)
    class Meta:
        app_label="lab"
        verbose_name="Предпросмотр импорта блока"
        verbose_name_plural="Предпросмотры импорта блоков"
        default_permissions=()
