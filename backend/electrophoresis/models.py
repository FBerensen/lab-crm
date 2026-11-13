from decimal import Decimal
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from lab.models import Dated, Task, LaboratoryProtocolRun, Lot, Sample, fk


class ElectrophoresisGel(Dated):
    class Status(models.TextChoices):
        DRAFT = ("draft", "Черновик")
        COMPLETED = ("completed", "Проведён")

    task = fk(Task, "Задача", related_name="electrophoresis_gels")
    laboratory_run = models.ForeignKey(
        LaboratoryProtocolRun,
        on_delete=models.PROTECT,
        verbose_name="Выполнение методики (выделение ДНК / ПЦР)",
        related_name="electrophoresis_gels",
        null=True,
        blank=True,
    )
    buffer_lot = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        verbose_name="Партия буфера (TAE/TBE)",
        related_name="electrophoresis_gels",
        null=True,
        blank=True,
    )
    agarose_percent = models.DecimalField(
        "Концентрация агарозы, %",
        max_digits=5,
        decimal_places=2,
        default=Decimal("1.50"),
        validators=[MinValueValidator(Decimal("0.10"))],
    )
    voltage_v = models.PositiveIntegerField("Напряжение, В", default=120)
    duration_min = models.PositiveIntegerField("Время фореза, мин", default=30)
    marker_name = models.CharField("Маркер молекулярного веса (Ladder)", max_length=120, blank=True)
    status = models.CharField("Статус", max_length=20, choices=Status.choices, default=Status.DRAFT)
    result_summary = models.TextField("Заключение по гелю", blank=True)

    def clean(self):
        super().clean()
        if self.laboratory_run_id and self.task_id and self.laboratory_run.task_id != self.task_id:
            raise ValidationError("Выполнение методики относится к другой задаче.")
        if self.buffer_lot_id and (not self.buffer_lot.released or self.buffer_lot.expired):
            raise ValidationError({"buffer_lot": "Партия буфера должна быть допущена и не просрочена."})

    def __str__(self):
        return f"Форез #{self.pk or 'новый'} · {self.actual_day} · {self.agarose_percent}% ({self.voltage_v} В)"

    class Meta:
        app_label = "lab"
        verbose_name = "Гель-электрофорез (Форез)"
        verbose_name_plural = "Форезы (гель-электрофорез)"
        ordering = ["-actual_day", "-created_at"]


class ElectrophoresisLane(models.Model):
    class QualityResult(models.TextChoices):
        PASS = ("pass", "Зачёт (чёткая полоса)")
        WEAK = ("weak", "Слабая полоса")
        SMEAR = ("smear", "Шмер / деградация ДНК")
        EMPTY = ("empty", "Отсутствие сигнала")
        CONTROL_OK = ("control_ok", "Контроль чистый")

    gel = models.ForeignKey(ElectrophoresisGel, on_delete=models.CASCADE, related_name="lanes", verbose_name="Гель")
    lane_number = models.PositiveSmallIntegerField("Дорожка №")
    sample = models.ForeignKey(Sample, on_delete=models.PROTECT, null=True, blank=True, verbose_name="Проба")
    is_control = models.BooleanField("Контроль / Маркер", default=False)
    sample_volume_ul = models.DecimalField(
        "Объём пробы, мкл",
        max_digits=6,
        decimal_places=2,
        default=Decimal("5.00"),
        validators=[MinValueValidator(Decimal("0.10"))],
    )
    band_size_bp = models.CharField("Размер фрагмента, п.н.", max_length=80, blank=True)
    quality = models.CharField("Оценка дорожки", max_length=20, choices=QualityResult.choices, default=QualityResult.PASS)
    note = models.CharField("Примечание", max_length=240, blank=True)

    def clean(self):
        super().clean()
        if not self.sample_id and not self.is_control:
            raise ValidationError("Укажите пробу или отметьте дорожку как контроль/маркер.")

    class Meta:
        app_label = "lab"
        verbose_name = "Дорожка фореза"
        verbose_name_plural = "Дорожки фореза"
        ordering = ["lane_number"]
        constraints = [
            models.UniqueConstraint(fields=["gel", "lane_number"], name="unique_gel_lane_number")
        ]
