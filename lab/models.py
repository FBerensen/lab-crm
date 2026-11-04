import re
from django.core.validators import MaxValueValidator
from django.core.exceptions import ValidationError

# Единицы измерения (п.9)
UNITS = ["л", "мл", "мкл", "кг", "г", "мкг", "шт", "упак"]


def validate_sample_series(value):
    if not value or not (2 <= len(value) <= 5):
        raise ValidationError("Серия должна содержать от 2 до 5 символов.")

def validate_sample_number(value):
    if not value:
        raise ValidationError("Укажите номер.")
    if not re.fullmatch(r"[0-9*.,:]+", value):
        raise ValidationError("Номер может содержать только цифры и символы * , . :")
    for n in re.findall(r"\d+", value):
        if not (1 <= int(n) <= 150):
            raise ValidationError(f"Число {n} вне диапазона 1–150.")



from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

USER = settings.AUTH_USER_MODEL



def lead(user):
    return bool(
        user and user.is_active and user.is_staff
        and (
            user.is_superuser
            or user.groups.filter(name="Руководители").exists()
        )
    )


def warehouse(user):
    return lead(user) or bool(
        user and user.is_active and user.is_staff
        and user.groups.filter(name="Склад").exists()
    )


def fk(model, label, **kwargs):
    return models.ForeignKey(
        model, on_delete=models.PROTECT,
        verbose_name=label, **kwargs
    )


def positive():
    return models.DecimalField(
        "Количество", max_digits=16, decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))]
    )


def convert_preparation_quantity(value, from_unit, to_unit):
    units = {
        "l": ("volume", Decimal("1000")),
        "л": ("volume", Decimal("1000")),
        "ml": ("volume", Decimal("1")),
        "мл": ("volume", Decimal("1")),
        "ul": ("volume", Decimal("0.001")),
        "мкл": ("volume", Decimal("0.001")),

        "kg": ("mass", Decimal("1000")),
        "кг": ("mass", Decimal("1000")),
        "g": ("mass", Decimal("1")),
        "г": ("mass", Decimal("1")),
        "mg": ("mass", Decimal("0.001")),
        "мг": ("mass", Decimal("0.001")),
        "ug": ("mass", Decimal("0.000001")),
        "мкг": ("mass", Decimal("0.000001")),
    }

    if from_unit not in units or to_unit not in units:
        raise ValidationError(
            f"Не поддерживается перевод единиц: "
            f"{from_unit} → {to_unit}."
        )

    source_group, source_factor = units[from_unit]
    target_group, target_factor = units[to_unit]

    if source_group != target_group:
        raise ValidationError(
            "Нельзя автоматически переводить массу в объём "
            "или объём в массу."
        )

    amount = Decimal(str(value))

    if not amount.is_finite() or amount <= 0:
        raise ValidationError(
            "Количество для пересчёта должно быть "
            "положительным конечным числом."
        )

    return amount * source_factor / target_factor

class Stamp(models.Model):
    actor = fk(USER, "Кто внёс запись")
    created_at = models.DateTimeField("Когда внесено", auto_now_add=True)

    class Meta:
        abstract = True

class BufferName(models.Model):
    class Kind(models.TextChoices):
        BUFFER = "buffer", "Буфер"
        SOLUTION = "solution", "Раствор"

    name = models.CharField(
        "Название раствора или буфера",
        max_length=240,
        unique=True,
    )

    kind = models.CharField(
        "Тип",
        max_length=20,
        choices=Kind.choices,
        blank=True,
        default="",
        help_text="Для существующих записей выберите тип вручную.",
    )

    concentration = models.CharField(
        "Концентрация / обозначение состава",
        max_length=120,
        blank=True,
        help_text=(
            "Например: 1 М, 70 % об./об., 10×. "
            "На этом этапе поле используется для описания, "
            "а не для автоматического расчёта."
        ),
    )

    storage_conditions = models.TextField(
        "Условия хранения",
        blank=True,
    )

    shelf_life_days = models.PositiveIntegerField(
        "Срок годности после приготовления, дней",
        null=True,
        blank=True,
        validators=[MinValueValidator(1)],
        help_text=(
            "Оставьте пустым, если срок задаётся отдельно "
            "для каждой приготовленной партии."
        ),
    )

    requires_autoclaving = models.BooleanField(
        "Требуется автоклавирование после приготовления",
        default=False,
        help_text=(
            "Отметьте, если это предусмотрено протоколом. "
            "Не является отметкой о выполненной обработке."
        ),
    )

    active = models.BooleanField(
        "Активен",
        default=True,
    )

    sort_order = models.PositiveIntegerField(
        "Порядок",
        default=100,
    )

    def __str__(self):
        if self.concentration:
            return f"{self.name} — {self.concentration}"
        return self.name

    class Meta:
        verbose_name = "Раствор или буфер"
        verbose_name_plural = "Растворы и буферы"
        ordering = ["sort_order", "name"]

class PreparationProtocol(models.Model):
    product = models.ForeignKey(
        BufferName,
        on_delete=models.PROTECT,
        verbose_name="Раствор или буфер",
        related_name="preparation_protocols",
    )

    title = models.CharField(
        "Название протокола",
        max_length=240,
    )

    version = models.CharField(
        "Версия",
        max_length=40,
        default="1",
    )

    base_volume_ml = models.DecimalField(
        "Конечный объём по протоколу, мл",
        max_digits=16,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
        help_text=(
            "Конечный объём готового раствора, "
            "на который рассчитаны количества компонентов."
        ),
    )

    instructions = models.TextField(
        "Порядок приготовления",
        help_text=(
            "Последовательность добавления компонентов, "
            "растворение и доведение до конечного объёма."
        ),
    )

    target_ph = models.CharField(
        "Требования к pH",
        max_length=120,
        blank=True,
        help_text="Целевое значение или диапазон, если применимо.",
    )

    processing_notes = models.TextField(
        "Фильтрация, стерилизация и особые условия",
        blank=True,
        help_text=(
            "Требования протокола к обработке. "
            "Это инструкция, а не запись о выполненной процедуре."
        ),
    )

    active = models.BooleanField(
        "Доступен для новых приготовлений",
        default=True,
    )

    created_at = models.DateTimeField(
        "Дата создания",
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.title} / версия {self.version} / {self.product}"

    class Meta:
        verbose_name = "Протокол приготовления"
        verbose_name_plural = "Протоколы приготовления"
        ordering = ["product__name", "title", "version"]
        constraints = [
            models.UniqueConstraint(
                fields=["product", "title", "version"],
                name="unique_prep_protocol_version",
            ),
            models.CheckConstraint(
                condition=models.Q(base_volume_ml__gt=0),
                name="prep_protocol_volume_positive",
            ),
        ]

class PreparationProtocolComponent(models.Model):
    class Unit(models.TextChoices):
        GRAM = "g", "г"
        MILLIGRAM = "mg", "мг"
        LITRE = "l", "л"
        MILLILITRE = "ml", "мл"
        MICROLITRE = "ul", "мкл"

    protocol = models.ForeignKey(
        PreparationProtocol,
        on_delete=models.PROTECT,
        verbose_name="Протокол приготовления",
        related_name="recipe_components",
    )

    position = models.PositiveIntegerField(
        "Порядок добавления",
        default=10,
    )

    reagent_name = models.CharField(
        "Компонент / реактив",
        max_length=240,
        help_text=(
            "Название требуемого компонента. "
            "Конкретная складская партия выбирается "
            "при фактическом приготовлении."
        ),
    )

    allowed_lots = models.ManyToManyField(
        "Lot",
        verbose_name="Допустимые складские партии",
        related_name="allowed_recipe_components",
        blank=True,
        help_text=(
            "Партии, разрешённые для этого компонента. "
            "При приготовлении дополнительно проверяются "
            "остаток, срок годности и допуск к использованию. "
            "Пустой список не разрешает использовать любые партии."
        ),
    )

    source_concentration = models.CharField(
        "Требуемая концентрация исходного компонента",
        max_length=120,
        blank=True,
        help_text=(
            "Укажите для исходного раствора. "
            "Для сухого реактива можно оставить пустым."
        ),
    )

    quantity = models.DecimalField(
        "Количество на базовый объём протокола",
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    unit = models.CharField(
        "Единица количества",
        max_length=10,
        choices=Unit.choices,
    )

    to_final_volume = models.BooleanField(
        "Довести до конечного объёма",
        default=False,
        help_text=(
            "Для растворителя, добавляемого до конечного "
            "объёма. В этом случае количество оставьте пустым."
        ),
    )

    final_concentration = models.CharField(
        "Конечная концентрация компонента",
        max_length=120,
        blank=True,
        help_text=(
            "Информационное поле. Не заменяет указание "
            "количества для фиксированной дозировки."
        ),
    )

    note = models.TextField(
        "Указания по добавлению",
        blank=True,
    )

    def available_lots(self):
        if self.pk is None:
            return Lot.objects.none()

        return (
            self.allowed_lots
            .filter(
                released=True,
                quantity__gt=models.F("reserved"),
            )
            .filter(
                models.Q(expiry__isnull=True)
                | models.Q(expiry__gte=timezone.localdate())
            )
            .order_by("expiry", "pk")
        )
    def required_quantity(self, volume_ml, target_unit=None):
        """
        Плановое количество компонента на заданный объём.

        Если target_unit не указан, результат возвращается
        в единице компонента рецептуры.

        Для доведения до конечного объёма возвращается None:
        фактический расход растворителя фиксируется отдельно.
        """
        volume = Decimal(str(volume_ml))

        if not volume.is_finite() or volume <= 0:
            raise ValidationError(
                "Объём приготовления должен быть "
                "положительным конечным числом."
            )

        base_volume = self.protocol.base_volume_ml

        if base_volume is None or base_volume <= 0:
            raise ValidationError(
                "В протоколе должен быть указан "
                "положительный базовый объём."
            )

        self.full_clean()

        if self.to_final_volume:
            return None

        required = self.quantity * volume / base_volume

        if target_unit is not None:
            required = convert_preparation_quantity(
                required,
                self.unit,
                target_unit,
            )

        return required

    def clean(self):
        super().clean()

        if self.to_final_volume:
            if self.quantity is not None:
                raise ValidationError({
                    "quantity": (
                        "Для доведения до конечного объёма "
                        "оставьте количество пустым."
                    ),
                })

            if self.unit not in (
                self.Unit.LITRE,
                self.Unit.MILLILITRE,
                self.Unit.MICROLITRE,
            ):
                raise ValidationError({
                    "unit": (
                        "Для доведения до конечного объёма "
                        "выберите единицу объёма."
                    ),
                })

        elif self.quantity is None:
            raise ValidationError({
                "quantity": "Укажите количество компонента.",
            })

    def __str__(self):
        if self.to_final_volume:
            return f"{self.reagent_name} — до конечного объёма"

        return (
            f"{self.reagent_name} — "
            f"{self.quantity} {self.get_unit_display()}"
        )

    class Meta:
        verbose_name = "Компонент протокола"
        verbose_name_plural = "Компоненты протокола"
        ordering = ["position", "pk"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(
                        to_final_volume=False,
                        quantity__isnull=False,
                        quantity__gt=0,
                    )
                    | models.Q(
                        to_final_volume=True,
                        quantity__isnull=True,
                        unit__in=["l", "ml", "ul"],
                    )
                ),
                name="recipe_component_quantity_valid",
            ),
        ]

class Dated(Stamp):
    actual_day = models.DateField(
        "Дата выполнения", default=timezone.localdate
    )

    def clean(self):
        super().clean()
        if self.actual_day and self.actual_day > timezone.localdate():
            raise ValidationError(
                "Выполненную работу нельзя датировать будущим числом."
            )

    class Meta:
        abstract = True


class Project(models.Model):
    title = models.CharField("Название", max_length=240)
    kind = models.CharField(
        "Тип", max_length=40,
        choices=[(x, x) for x in (
            "Госзадание", "Проект", "Аспирантская работа"
        )]
    )
    manager = fk(USER, "Руководитель")
    due = models.DateField("Срок", blank=True, null=True)
    description = models.TextField("Описание", blank=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"


class Sample(models.Model):
    series = models.CharField(
        "Серия", max_length=5,
        validators=[validate_sample_series],
    )
    number = models.CharField(
        "Номер", max_length=20,
        validators=[validate_sample_number],
        help_text="Числа 1–150, можно с символами * , . :",
    )
    project = fk(Project, "Проект")
    parent = fk("self", "Исходный образец/проба", blank=True, null=True)
    kind = models.CharField(
        "Тип материала", max_length=60,
        choices=[(x, x) for x in (
            "Коллекционный образец", "Растение", "Тканевая проба",
            "ДНК", "РНК", "Аликвота", "Другое"
        )]
    )
    location = models.CharField("Место хранения", max_length=240)
    note = models.TextField("Примечание", blank=True)

    @property
    def code(self):
        return f"{self.series} №{self.number}"

    def clean(self):
        super().clean()
        if self.parent_id:
            if self.parent_id == self.pk:
                raise ValidationError("Образец не может ссылаться на себя.")
            if self.parent.project_id != self.project_id:
                raise ValidationError(
                    "В пилоте производная проба должна относиться "
                    "к тому же проекту, что и исходная."
                )

    def __str__(self):
        return self.code

    class Meta:
        verbose_name = "Образец/Проба"
        verbose_name_plural = "Образцы/Пробы"
        constraints = [
            models.UniqueConstraint(
                fields=["series", "number"],
                name="unique_sample_series_number",
            ),
        ]

STAGES = [
    "Получение материала", "Фиксация материала", "Выделение ДНК/РНК",
    "Создание блоков", "Постановка ПЦР", "Маркерный анализ",
    "Анализ результатов", "Проверка результатов",
]


class Task(models.Model):
    title = models.CharField("Задача", max_length=240)
    project = fk(Project, "Проект")
    sample = fk(Sample, "Образец / проба", blank=True, null=True)
    samples = models.ManyToManyField(
        Sample,
        verbose_name="Образцы / пробы",
        blank=True,
        related_name="tasks",
    )
    owner = fk(USER, "Исполнитель", related_name="lab_tasks")
    reviewer = fk(USER, "Проверяющий", related_name="lab_reviews")
    due = models.DateField("Срок")
    stage = models.CharField(
        "Этап", max_length=80,
        choices=[(x, x) for x in STAGES],
        default=STAGES[0],
    )
    status = models.CharField(
        "Состояние", max_length=30,
        choices=[(x, x) for x in (
            "Назначено", "В работе", "Остановлено",
            "На проверке", "Завершено"
        )],
        default="Назначено",
    )
    parent = fk("self", "Повтор задачи", blank=True, null=True)
    result = models.TextField("Текущий результат", blank=True)
    blocker = models.TextField("Причина остановки", blank=True)
    next_action = models.TextField("Следующее действие", blank=True)
    next_actor = fk(
        USER, "Кто устраняет препятствие",
        related_name="lab_blockers", blank=True, null=True
    )
    expected = models.DateField("Ожидаемая дата", blank=True, null=True)
    changed_at = models.DateTimeField("Обновлено", auto_now=True)

    def clean(self):
        super().clean()
        if self.owner_id and self.reviewer_id:
            if self.owner_id == self.reviewer_id:
                raise ValidationError(
                    "Исполнитель и проверяющий должны быть разными людьми."
                )
            for user in (self.owner, self.reviewer):
                if not user.is_active or not user.is_staff:
                    raise ValidationError(
                        "Выбирайте активных сотрудников с доступом к системе."
                    )
            if not lead(self.reviewer):
                raise ValidationError(
                    "Проверяющий должен входить в группу «Руководители»."
                )
        if self.sample_id and self.sample.project_id != self.project_id:
            raise ValidationError("Проба относится к другому проекту.")

    def __str__(self):
        return f"#{self.pk} · {self.title}"

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"


EVENTS = [
    ("accept", "Принять в работу"),
    ("block", "Остановить с указанием причины"),
    ("resume", "Возобновить"),
    ("submit", "Передать результат на проверку"),
    ("approve", "Подтвердить результат"),
    ("return", "Вернуть с замечаниями"),
    ("transfer", "Передать другому исполнителю"),
    ("repeat", "Создать повтор"),
    ("plan", "Изменить срок"),
    ("comment", "Комментарий / смена этапа"),
]


class TaskEvent(Stamp):
    task = fk(Task, "Задача")
    action = models.CharField(
        "Действие", max_length=20, choices=EVENTS
    )
    note = models.TextField(
        "Результат / причина / комментарий", blank=True
    )
    stage = models.CharField(
        "Новый этап",
        max_length=80,
        choices=[(x, x) for x in STAGES],
        blank=True,
    )
    target = fk(
        USER,
        "Новый исполнитель / кто устраняет препятствие",
        blank=True,
        null=True,
        related_name="targeted_task_events",
    )
    next_action = models.TextField(
        "Следующее действие", blank=True
    )
    expected = models.DateField(
        "Ожидаемая дата / новый срок",
        blank=True,
        null=True,
    )

    def clean(self):
        super().clean()
        if not self.task_id or not self.actor_id or not self.action:
            return
        t, u = self.task, self.actor

        if not u.is_active or not u.is_staff:
            raise ValidationError("Нет доступа.")

        if self.action in ("approve", "return"):
            if u.pk != t.reviewer_id or u.pk == t.owner_id:
                raise ValidationError(
                    "Решение принимает назначенный проверяющий, "
                    "не являющийся исполнителем."
                )
        elif self.action in ("transfer", "repeat", "plan"):
            if not lead(u):
                raise ValidationError("Это действие доступно руководителю.")
        elif u.pk != t.owner_id and not lead(u):
            raise ValidationError("Вы не исполнитель этой задачи.")

        allowed = {
            "accept": ["Назначено"],
            "resume": ["Остановлено"],
            "block": ["В работе"],
            "submit": ["В работе"],
            "approve": ["На проверке"],
            "return": ["На проверке"],
            "transfer": ["Назначено", "В работе", "Остановлено"],
            "plan": ["Назначено", "В работе", "Остановлено", "На проверке"],
            "repeat": ["Завершено", "На проверке", "Остановлено", "В работе"],
            "comment": ["Назначено", "В работе", "Остановлено", "На проверке"],
        }
        if t.status not in allowed.get(self.action, []):
            raise ValidationError(
                "Действие недоступно в текущем состоянии задачи."
            )

        if self.action in ("accept", "resume", "submit"):
            if u.pk != t.owner_id:
                raise ValidationError(
                    "Это действие должен подтвердить сам исполнитель."
                )

        if self.action in ("block", "submit", "return", "transfer", "plan"):
            if not self.note.strip():
                raise ValidationError(
                    "Укажите результат, причину или замечания."
                )

        if self.target_id:
            if not self.target.is_active or not self.target.is_staff:
                raise ValidationError("Выбран неактивный сотрудник.")

        if self.action == "block":
            if not self.target_id or not self.expected or not self.next_action.strip():
                raise ValidationError(
                    "Для остановки нужны ответственный, "
                    "следующее действие и ожидаемая дата."
                )

        if self.action == "transfer":
            if not self.target_id or self.target_id == t.reviewer_id:
                raise ValidationError(
                    "Нужен новый исполнитель, отличный от проверяющего."
                )

        if self.action == "plan" and not self.expected:
            raise ValidationError("Укажите новый срок.")

        if self.action == "repeat" and not self.expected:
            raise ValidationError("Укажите срок повторной задачи.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("События нельзя перезаписывать.")

        with transaction.atomic():
            self.task = Task.objects.select_for_update().get(pk=self.task_id)
            self.full_clean()
            t = self.task

            if self.action in ("accept", "resume", "return"):
                t.status = "В работе"
            elif self.action == "block":
                t.status = "Остановлено"
                t.blocker = self.note
                t.next_action = self.next_action
                t.next_actor = self.target
                t.expected = self.expected
            elif self.action == "submit":
                t.status = "На проверке"
                t.result = self.note
            elif self.action == "approve":
                t.status = "Завершено"
            elif self.action == "transfer":
                t.owner = self.target
                t.status = "Назначено"
            elif self.action == "plan":
                t.due = self.expected
            elif self.action == "repeat":
                repeat = Task(
                    title=t.title + " — повтор",
                    project=t.project,
                    sample=t.sample,
                    owner=t.owner,
                    reviewer=t.reviewer,
                    due=self.expected,
                    stage=t.stage,
                    parent=t,
                )
                repeat.full_clean()
                repeat.save()
                repeat.samples.set(t.samples.all())
                if t.sample_id:
                    repeat.samples.add(t.sample_id)

            if self.stage and self.action != "repeat":
                t.stage = self.stage

            if t.status != "Остановлено":
                t.blocker = ""
                t.next_action = ""
                t.next_actor = None
                t.expected = None

            t.full_clean()
            t.save()
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.task} · {self.get_action_display()}"

    class Meta:
        verbose_name = "Действие по задаче"
        verbose_name_plural = "Действия и история задач"
        ordering = ["-created_at"]


class WorkLog(Dated):
    task = fk(Task, "Задача", blank=True, null=True)
    operation = models.CharField("Выполненная работа", max_length=240)
    detail = models.TextField("Подробности / результат")
    correction_of = fk(
        "self", "Исправление записи",
        blank=True, null=True
    )

    def clean(self):
        super().clean()
        if self.correction_of_id:
            if self.correction_of.actor_id != self.actor_id:
                raise ValidationError(
                    "Исправить можно только собственную запись."
                )
        if self.task_id and self.actor_id:
            if self.task.owner_id != self.actor_id and not lead(self.actor):
                raise ValidationError(
                    "Запись по задаче вносит её исполнитель или руководитель."
                )

    def __str__(self):
        return f"{self.actual_day} · {self.operation}"

    class Meta:
        verbose_name = "Запись журнала работ"
        verbose_name_plural = "Журнал работ по дням"
        ordering = ["-actual_day", "-created_at"]


def new_stock_code():
    return "STK-" + uuid4().hex.upper()


class ProductCategory(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)
    active = models.BooleanField("Доступна для выбора", default=True)
    sort_order = models.PositiveIntegerField("Порядок в списке", default=100)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Категория внесения"
        verbose_name_plural = "Категории внесения"
        ordering = ["sort_order", "name"]


class StoragePlace(models.Model):
    name = models.CharField("Название", max_length=240, unique=True)
    active = models.BooleanField("Доступно для выбора", default=True)
    sort_order = models.PositiveIntegerField("Порядок в списке", default=100)

    def save(self, *args, **kwargs):
        with transaction.atomic():
            super().save(*args, **kwargs)
            # Keep the original text field compatible with existing reports.
            name = type(self).objects.values_list("name", flat=True).get(pk=self.pk)
            Lot.objects.filter(storage_place_id=self.pk).update(location=name)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Место хранения"
        verbose_name_plural = "Места хранения"
        ordering = ["sort_order", "name"]


class Lot(models.Model):
    code = models.CharField(
        "Внутренний код", max_length=100, unique=True,
        default=new_stock_code, editable=False,
    )
    category = fk(
        ProductCategory, "Внесение", blank=True, null=True,
        related_name="products",
    )
    name = models.CharField("Наименование продукта", max_length=240)
    manufacturer = models.CharField("Изготовитель", max_length=240, blank=True)
    catalog_number = models.CharField("Кат. номер", max_length=120, blank=True)
    unit = models.CharField(
        "Единица учёта", max_length=20,
        choices=[(x, x) for x in UNITS],
    )

    storage_place = fk(
        StoragePlace, "Место хранения", blank=True, null=True,
        related_name="products",
    )
    location = models.CharField(
        "Прежнее место хранения", max_length=240, blank=True, editable=False,
    )
    initial_quantity = models.DecimalField(
        "Количество", max_digits=16, decimal_places=4,
        blank=True, null=True,
        validators=[MinValueValidator(Decimal("0"))],
        help_text=(
            "Количество при создании карточки, в выбранной единице учёта. "
            "Будет проведено поступлением один раз. Для новой карточки "
            "приготовления или автоклавирования укажите 0."
        ),
    )
    expiry = models.DateField("Годен до", null=True, blank=True)
    minimum = models.DecimalField(
        "Минимальный свободный остаток",
        max_digits=16, decimal_places=4, default=0,
        validators=[MinValueValidator(0)],
    )
    quantity = models.DecimalField(
        "Физический остаток", max_digits=16,
        decimal_places=4, default=0,
    )
    reserved = models.DecimalField(
        "В резерве", max_digits=16, decimal_places=4, default=0,
    )
    released = models.BooleanField("Допущен к использованию", default=True)

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is None or "storage_place" in update_fields:
            if self.storage_place_id:
                self.location = self.storage_place.name
            if update_fields is not None:
                kwargs["update_fields"] = set(update_fields) | {"location"}
        super().save(*args, **kwargs)

    @property
    def free(self):
        return self.quantity - self.reserved

    @property
    def expired(self):
        return bool(self.expiry and self.expiry < timezone.localdate())

    def __str__(self):
        from .presentation import lot_label
        return lot_label(self)

    class Meta:
        verbose_name = "Продукт на склад"
        verbose_name_plural = "Продукты на складе"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0),
                name="lot_quantity_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(reserved__gte=0) & Q(reserved__lte=F("quantity")),
                name="lot_reservation_bounds",
            ),
            models.CheckConstraint(
                condition=Q(initial_quantity__isnull=True) | Q(initial_quantity__gte=0),
                name="lot_initial_quantity_nonnegative",
            ),
        ]

MOVE_TYPES = [
    ("receipt", "Поступление"),
    ("reserve", "Резервирование"),
    ("release", "Снятие резерва"),
    ("use_reserved", "Расход из резерва"),
    ("use_free", "Расход свободного остатка"),
    ("writeoff", "Списание свободного остатка"),
]


class Movement(Dated):
    lot = fk(Lot, "Продукт")
    kind = models.CharField("Операция", max_length=20, choices=MOVE_TYPES)
    qty = positive()
    task = fk(Task, "Задача", null=True, blank=True)
    preparation = fk(
        "Preparation", "Подготовка партии", null=True, blank=True
    )
    note = models.TextField("Основание / комментарий")

    def clean(self):
        super().clean()
        if self.kind in ("reserve", "release", "use_reserved") and not self.task_id:
            raise ValidationError("Для работы с резервом выберите задачу.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Складские операции нельзя перезаписывать.")

        with transaction.atomic():
            self.lot = Lot.objects.select_for_update().get(pk=self.lot_id)
            self.full_clean()
            lot, q = self.lot, self.qty

            if self.kind in ("reserve", "use_reserved", "use_free"):
                if not lot.released or lot.expired:
                    raise ValidationError(
                        "Партия просрочена или не допущена к использованию."
                    )

            if self.kind == "receipt":
                produced = Preparation.objects.filter(output_id=lot.pk).first()
                if produced and self.preparation_id != produced.pk:
                    raise ValidationError(
                        "Для изготовленной партии нельзя вручную "
                        "добавлять поступление."
                    )
                lot.quantity += q

            elif self.kind == "reserve":
                if q > lot.free:
                    raise ValidationError("Недостаточно свободного остатка.")
                lot.reserved += q

            elif self.kind in ("release", "use_reserved"):
                def total(kind):
                    return (
                        Movement.objects.filter(
                            lot=lot, task_id=self.task_id, kind=kind
                        ).aggregate(s=Sum("qty"))["s"]
                        or Decimal("0")
                    )

                own_reserve = (
                    total("reserve") - total("release") - total("use_reserved")
                )
                if q > own_reserve:
                    raise ValidationError(
                        "Количество превышает резерв именно этой задачи."
                    )
                lot.reserved -= q
                if self.kind == "use_reserved":
                    lot.quantity -= q

            elif self.kind in ("use_free", "writeoff"):
                if q > lot.free:
                    raise ValidationError("Недостаточно свободного остатка.")
                lot.quantity -= q

            lot.save(update_fields=["quantity", "reserved"])
            super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.lot.code} · {self.get_kind_display()} · {self.qty}"

    class Meta:
        verbose_name = "Складская операция"
        verbose_name_plural = "Поступления, резервы и расход"
        ordering = ["-created_at"]


class Preparation(Dated):
    kind = models.CharField(
        "Вид подготовки",
        max_length=40,
        choices=[(x, x) for x in (
            "Приготовление реактива",
            "Автоклавирование расходников",
        )],
    )

    output = models.OneToOneField(
        Lot,
        on_delete=models.PROTECT,
        verbose_name="Готовая партия",
        related_name="production_record",
        null=True,
        blank=True,
        help_text="Создаётся автоматически при проведении.",
    )

    output_category = models.ForeignKey(
        ProductCategory,
        on_delete=models.PROTECT,
        verbose_name="Категория готовой партии",
        related_name="+",
        null=True,
        blank=True,
    )

    output_storage_place = models.ForeignKey(
        StoragePlace,
        on_delete=models.PROTECT,
        verbose_name="Место хранения готовой партии",
        related_name="+",
        null=True,
        blank=True,
    )

    output_expiry = models.DateField(
        "Срок годности готовой партии",
        null=True,
        blank=True,
        help_text=(
            "Последний день использования. "
            "Если не указан, срок можно рассчитать "
            "по справочнику растворов и буферов."
        ),
    )

    qty = positive()
    protocol = models.CharField(
        "СОП / протокол / идентификатор цикла", max_length=240
    )
    recipe = models.ForeignKey(
        PreparationProtocol,
        on_delete=models.PROTECT,
        verbose_name="Протокол приготовления",
        related_name="preparations",
        null=True,
        blank=True,
        help_text=(
            "Рецептура для приготовления раствора или буфера. "
            "Для старых записей может быть не указана."
        ),
    )

    planned_volume_ml = models.DecimalField(
        "Планируемый конечный объём, мл",
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    actual_volume_ml = models.DecimalField(
        "Фактический конечный объём, мл",
        max_digits=16,
        decimal_places=4,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )
    note = models.TextField("Результат и замечания", blank=True)
    processed = models.BooleanField("Проведено по складу", default=False)
    approved_by = fk(
        USER, "Кто допустил партию",
        blank=True, null=True, related_name="+"
    )
    approved_at = models.DateTimeField(
        "Дата допуска", null=True, blank=True
    )

    autoclaving_required = models.BooleanField(
        "Требовалось автоклавирование при приготовлении",
        null=True,
        default=None,
        editable=False,
        help_text="Фиксируется при проведении. Пусто — историческая запись без снимка требования.",
    )

    approved_cycle = models.ForeignKey(
        "Autoclaving",
        on_delete=models.PROTECT,
        verbose_name="Цикл, учтённый при допуске",
        related_name="approved_preparations",
        null=True,
        blank=True,
        editable=False,
    )

    autoclaving_done = models.BooleanField("Автоклавирование всей партии выполнено", default=False, editable=False)
    autoclaved_by = fk(USER, "Кто отметил автоклавирование", null=True, blank=True, related_name="+", editable=False)
    autoclaved_at = models.DateTimeField("Когда отмечено автоклавирование", null=True, blank=True, editable=False)

    def complete(self):
        with transaction.atomic():
            p = Preparation.objects.select_for_update().get(pk=self.pk)
            if p.processed:
                raise ValidationError("Партия уже проведена по складу.")

            components = list(p.components.all())
            if not components:
                raise ValidationError("Добавьте исходные партии.")

            if not p.output_id:
                if not p.recipe_id:
                    raise ValidationError(
                        "Выберите протокол приготовления."
                    )

                if (
                    p.actual_volume_ml is None
                    or p.actual_volume_ml <= 0
                ):
                    raise ValidationError(
                        "Укажите положительный фактический "
                        "объём готового раствора."
                    )

                if not p.output_category_id:
                    raise ValidationError(
                        "Выберите категорию готовой партии."
                    )

                if not p.output_storage_place_id:
                    raise ValidationError(
                        "Выберите место хранения готовой партии."
                    )

                if not p.output_category.active:
                    raise ValidationError(
                        "Выбранная категория неактивна."
                    )

                if not p.output_storage_place.active:
                    raise ValidationError(
                        "Выбранное место хранения неактивно."
                    )

                expiry = p.output_expiry
                product = p.recipe.product

                if expiry is None and product.shelf_life_days:
                    from datetime import timedelta

                    expiry = (
                        p.actual_day
                        + timedelta(days=product.shelf_life_days)
                    )

                if expiry is not None:
                    if expiry < p.actual_day:
                        raise ValidationError(
                            "Срок годности не может быть раньше "
                            "даты приготовления."
                        )

                    if expiry < timezone.localdate():
                        raise ValidationError(
                            "Срок годности готовой партии "
                            "уже истёк."
                        )

                new_output = Lot(
                    category=p.output_category,
                    name=str(product),
                    unit="мл",
                    storage_place=p.output_storage_place,
                    expiry=expiry,
                    initial_quantity=Decimal("0"),
                    quantity=Decimal("0"),
                    reserved=Decimal("0"),
                    released=False,
                )

                new_output.full_clean()
                new_output.save()

                p.output = new_output
                p.qty = p.actual_volume_ml

                p.save(update_fields=["output", "qty"])

            ids = sorted({p.output_id, *[c.lot_id for c in components]})
            locked = {
                x.pk: x
                for x in Lot.objects.select_for_update()
                .filter(pk__in=ids).order_by("pk")
            }
            output = locked[p.output_id]
            # Новые проведения выполняются по рецептуре.
            if p.kind != "Приготовление реактива":
                raise ValidationError(
                    "Для автоклавирования используйте отдельный "
                    "журнал. Эта операция предназначена "
                    "для приготовления растворов и буферов."
                )

            if not p.recipe_id:
                raise ValidationError(
                    "Выберите протокол приготовления."
                )

            recipe = p.recipe

            if not recipe.active or not recipe.product.active:
                raise ValidationError(
                    "Выбранный протокол или готовый продукт "
                    "недоступен для новых приготовлений."
                )

            if (
                p.planned_volume_ml is None
                or p.planned_volume_ml <= 0
                or p.actual_volume_ml is None
                or p.actual_volume_ml <= 0
            ):
                raise ValidationError(
                    "Укажите положительные планируемый "
                    "и фактический конечные объёмы."
                )
            expected_output_qty = convert_preparation_quantity(
                p.actual_volume_ml,
                "ml",
                output.unit,
            )

            if expected_output_qty != expected_output_qty.quantize(
                Decimal("0.0001")
            ):
                raise ValidationError(
                    "Фактический объём нельзя точно записать "
                    "в выбранной складской единице с точностью "
                    "до четырёх знаков после запятой. "
                    "Выберите более мелкую единицу учёта "
                    "для готовой партии."
                )

            if p.qty != expected_output_qty:
                raise ValidationError(
                    "Количество готовой партии не соответствует "
                    "фактическому объёму. "
                    f"Для {p.actual_volume_ml} мл укажите "
                    f"{expected_output_qty} {output.unit}."
                )

            required_ids = set(
                recipe.recipe_components.values_list(
                    "pk", flat=True
                )
            )

            if not required_ids:
                raise ValidationError(
                    "В выбранном протоколе нет компонентов."
                )

            used_ids = set()
            totals_by_lot = {}

            for c in components:
                # Проверяет количество, связи и допустимую партию.
                c.full_clean()

                if not c.recipe_component_id:
                    raise ValidationError(
                        "Для каждой строки расхода выберите "
                        "компонент рецептуры."
                    )

                if c.recipe_component_id not in required_ids:
                    raise ValidationError(
                        "В расходе есть компонент "
                        "другого протокола."
                    )

                if c.lot_id == p.output_id:
                    raise ValidationError(
                        "Готовая партия не может быть "
                        "собственным компонентом."
                    )

                                              # Проверяем совместимость единиц.
                convert_preparation_quantity(
                    c.qty,
                    locked[c.lot_id].unit,
                    c.recipe_component.unit,
                )

                used_ids.add(c.recipe_component_id)

                totals_by_lot[c.lot_id] = (
                    totals_by_lot.get(c.lot_id, Decimal("0"))
                    + c.qty
                )

            if required_ids - used_ids:
                raise ValidationError(
                    "Укажите фактический расход для каждого "
                    "компонента протокола, включая растворитель."
                )

            for lot_id, total_qty in totals_by_lot.items():
                lot = locked[lot_id]

                if not lot.released:
                    raise ValidationError(
                        f"Партия «{lot}» не допущена "
                        "к использованию."
                    )

                if lot.expired:
                    raise ValidationError(
                        f"Срок годности партии «{lot}» истёк."
                    )

                if total_qty > lot.free:
                    raise ValidationError(
                        f"Недостаточно партии «{lot}». "
                        f"Требуется: {total_qty} {lot.unit}; "
                        f"доступно: {lot.free} {lot.unit}."
                    )

            if output.quantity != 0 or output.reserved != 0:
                raise ValidationError(
                    "Готовая партия должна быть новой, с нулевым остатком."
                )
            if Movement.objects.filter(lot=output).exists():
                raise ValidationError(
                    "Для готовой партии создайте отдельную новую карточку."
                )

            for c in components:
                if c.lot_id == p.output_id:
                    raise ValidationError(
                        "Готовая партия не может быть собственным компонентом."
                    )
                Movement.objects.create(
                    actor=p.actor,
                    actual_day=p.actual_day,
                    lot_id=c.lot_id,
                    kind="use_free",
                    qty=c.qty,
                    preparation=p,
                    note=f"{p.kind}: {p.protocol}",
                )

            output.released = False
            output.save(update_fields=["released"])

            Movement.objects.create(
                actor=p.actor,
                actual_day=p.actual_day,
                lot=output,
                kind="receipt",
                qty=p.qty,
                preparation=p,
                note=f"Получено при подготовке: {p.protocol}",
            )
            Preparation.objects.filter(pk=p.pk).update(
                processed=True,
                autoclaving_required=bool(recipe.product.requires_autoclaving),
            )

    def mark_autoclaved(self, user, *, confirmed_full_batch=False):
        from .preparation_access import can_mark_autoclaving
        with transaction.atomic():
            p = Preparation.objects.select_for_update().get(pk=self.pk)
            if not can_mark_autoclaving(user, p):
                raise ValidationError("Нет права отмечать обработку этого приготовления.")
            if not confirmed_full_batch:
                raise ValidationError("Подтвердите обработку всей исходной партии.")
            if not p.processed or not p.output_id:
                raise ValidationError("Сначала проведите приготовление.")
            if p.autoclaving_required is not True:
                raise ValidationError("Обработка не требуется либо требование исторической записи неизвестно.")
            if p.approved_at is not None:
                raise ValidationError("Допущенную партию изменять нельзя.")
            if p.autoclaving_done:
                raise ValidationError("Обработка уже отмечена. Перезапись запрещена.")
            lot = Lot.objects.select_for_update().get(pk=p.output_id)
            if lot.released or lot.quantity != p.qty or lot.reserved != 0:
                raise ValidationError("Статус или остаток партии изменён. Нужна отдельная проверка.")
            if lot.expired:
                raise ValidationError("Срок годности партии истёк.")
            p.autoclaving_done = True
            p.autoclaved_by = user
            p.autoclaved_at = timezone.now()
            p.save(update_fields=["autoclaving_done", "autoclaved_by", "autoclaved_at"])
            Audit.objects.create(actor=user, entity=p._meta.label, object_id=str(p.pk),
                detail=f"Отмечено автоклавирование всей партии: {p.qty} {lot.unit}. Без отдельного утверждения цикла.")
        self.refresh_from_db()

    def approve(self, user):
        with transaction.atomic():
            from .preparation_access import can_approve_preparation
            if not can_approve_preparation(user):
                raise ValidationError("Нет права допуска: проверьте роль и разрешение допуска приготовлений.")

            p = Preparation.objects.select_for_update().get(pk=self.pk)
            if not p.processed or not p.output_id:
                raise ValidationError("Сначала проведите приготовление.")
            if p.approved_at is not None:
                raise ValidationError("Партия уже допущена.")
            if p.autoclaving_required is None:
                raise ValidationError(
                    "Историческая запись: требование автоклавирования "
                    "не было зафиксировано. Нужна отдельная проверка; "
                    "автоматический допуск запрещён."
                )

            lot = Lot.objects.select_for_update().get(pk=p.output_id)
            if lot.expired:
                raise ValidationError("Срок годности готовой партии истёк.")
            if lot.released:
                raise ValidationError(
                    "Партия уже отмечена допущенной вне этой процедуры. "
                    "Проверьте историю склада."
                )
            if lot.quantity != p.qty or lot.reserved != 0:
                raise ValidationError(
                    "Остаток готовой партии изменён либо есть резерв. "
                    "Допуск всей исходной партии требует отдельной проверки."
                )

            cycle = None
            if p.autoclaving_required and p.autoclaving_done:
                if p.autoclaved_by_id is None or p.autoclaved_at is None:
                    raise ValidationError("Отметка автоклавирования неполна.")
                later_cycles = AutoclavingItem.objects.filter(
                    lot_id=lot.pk, cycle__created_at__gt=p.autoclaved_at,
                )
                if later_cycles.exists():
                    raise ValidationError("После отметки зарегистрирован новый цикл. Нужна отдельная проверка истории обработки.")
            if p.autoclaving_required and not p.autoclaving_done:
                cycle_ids = AutoclavingItem.objects.filter(
                    lot_id=lot.pk
                ).values("cycle_id")
                cycle = (
                    Autoclaving.objects.select_for_update()
                    .filter(pk__in=cycle_ids)
                    .order_by("-actual_day", "-created_at", "-pk")
                    .first()
                )
                if cycle is None:
                    raise ValidationError(
                        "Требуется зарегистрировать автоклавирование партии."
                    )
                if cycle.status != Autoclaving.Status.COMPLETED:
                    raise ValidationError(
                        "Последний зарегистрированный цикл не завершён "
                        "успешно. Допуск запрещён."
                    )
                if (
                    cycle.actual_day < p.actual_day
                    or cycle.created_at < p.created_at
                ):
                    raise ValidationError(
                        "Цикл зарегистрирован раньше приготовления."
                    )
                if not cycle.control_result.strip():
                    raise ValidationError("Не указан результат контроля цикла.")
                if (
                    cycle.temperature_c is None
                    or cycle.holding_time_min is None
                ):
                    raise ValidationError("Не заполнены параметры цикла.")
                cycle.full_clean()
                items = list(cycle.items.filter(lot_id=lot.pk))
                total = Decimal("0")
                for item in items:
                    item.full_clean()
                    total += item.quantity
                if total != p.qty:
                    raise ValidationError(
                        "Последний цикл должен охватывать всю исходную "
                        f"партию: {p.qty} {lot.unit}. "
                        f"В загрузке указано: {total} {lot.unit}. "
                        "Частичную обработку разных циклов не суммируем."
                    )

            p.approved_by = user
            p.approved_at = timezone.now()
            p.approved_cycle = cycle
            p.save(update_fields=[
                "approved_by", "approved_at", "approved_cycle",
            ])
            lot.released = True
            lot.save(update_fields=["released"])
            Audit.objects.create(
                actor=user,
                entity=p._meta.label,
                object_id=str(p.pk),
                detail=(
                    "Допуск готовой партии; "
                    + (f"цикл #{cycle.pk}" if cycle else (f"отметка обработки {p.autoclaved_at.isoformat()}" if p.autoclaving_required else "автоклавирование не требовалось"))
                ),
            )
        self.refresh_from_db()

    def __str__(self):
        from .presentation import preparation_label
        return preparation_label(self)

    class Meta:
        verbose_name = 'Приготовление раствора или буфера'
        verbose_name_plural = 'Приготовление растворов и буферов'
        permissions = [("approve_preparation", "Может допускать приготовленные растворы и буферы")]
        constraints = [
            models.CheckConstraint(
                condition=(models.Q(autoclaving_done=False, autoclaved_by__isnull=True, autoclaved_at__isnull=True)
                    | models.Q(autoclaving_done=True, autoclaved_by__isnull=False, autoclaved_at__isnull=False)),
                name="prep_autoclaving_mark_complete",
            ),
        ]


class Component(models.Model):
    preparation = fk(Preparation, "Подготовка", related_name="components")
    lot = fk(Lot, "Исходная партия")
    qty = positive()
    recipe_component = models.ForeignKey(
        PreparationProtocolComponent,
        on_delete=models.PROTECT,
        verbose_name="Компонент рецептуры",
        related_name="actual_components",
        null=True,
        blank=True,
        help_text=(
            "Укажите, для какого компонента протокола "
            "используется эта складская партия."
        ),
    )
    def clean(self):
        super().clean()

        errors = {}

        preparation = (
            self.preparation
            if self.preparation_id
            else None
        )

        recipe_component = (
            self.recipe_component
            if self.recipe_component_id
            else None
        )

        if preparation is not None:
            if preparation.recipe_id:
                if recipe_component is None:
                    errors["recipe_component"] = (
                        "Выберите компонент рецептуры."
                    )
                elif (
                    recipe_component.protocol_id
                    != preparation.recipe_id
                ):
                    errors["recipe_component"] = (
                        "Этот компонент относится "
                        "к другому протоколу."
                    )

            elif recipe_component is not None:
                errors["recipe_component"] = (
                    "Сначала выберите протокол "
                    "в записи приготовления."
                )

            if (
                self.lot_id
                and self.lot_id == preparation.output_id
            ):
                errors["lot"] = (
                    "Готовая партия не может быть "
                    "собственным исходным компонентом."
                )

        if recipe_component is not None and self.lot_id:
            allowed = recipe_component.allowed_lots.filter(
                pk=self.lot_id
            ).exists()

            if not allowed:
                errors["lot"] = (
                    "Эта складская партия не разрешена "
                    "для выбранного компонента рецептуры."
                )

        if errors:
            raise ValidationError(errors)

    class Meta:
        verbose_name = "Исходная партия"
        verbose_name_plural = "Исходные партии и фактический расход"


class Autoclaving(Dated):
    class Status(models.TextChoices):
        DRAFT = "draft", "Черновик"
        COMPLETED = "completed", "Цикл завершён"
        FAILED = "failed", "Цикл неуспешен"

    equipment = models.CharField(
        "Автоклав / инвентарный номер",
        max_length=240,
    )

    cycle_number = models.CharField(
        "Номер цикла",
        max_length=100,
        blank=True,
    )

    protocol = models.CharField(
        "СОП / программа обработки",
        max_length=240,
    )

    temperature_c = models.DecimalField(
        "Температура выдержки, °C",
        max_digits=6,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
    )

    holding_time_min = models.DecimalField(
        "Время выдержки, мин",
        max_digits=8,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(Decimal("0.01"))],
        help_text="Продолжительность выдержки, не всего цикла.",
    )

    status = models.CharField(
        "Статус",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
    )

    control_result = models.TextField(
        "Результат контроля цикла",
        blank=True,
        help_text=(
            "Показания, индикаторы и другие результаты "
            "контроля по лабораторному протоколу."
        ),
    )

    note = models.TextField(
        "Замечания",
        blank=True,
    )

    def __str__(self):
        cycle = self.cycle_number or self.pk or "новый"
        return f"{self.actual_day} / {self.equipment} / цикл {cycle}"

    class Meta:
        verbose_name = "Автоклавирование"
        verbose_name_plural = "Автоклавирование"
        ordering = ["-actual_day", "-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(temperature_c__isnull=True)
                    | models.Q(temperature_c__gt=0)
                ),
                name="autoclave_temperature_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(holding_time_min__isnull=True)
                    | models.Q(holding_time_min__gt=0)
                ),
                name="autoclave_holding_time_positive",
            ),
        ]


class AutoclavingItem(models.Model):
    cycle = models.ForeignKey(
        Autoclaving,
        on_delete=models.PROTECT,
        verbose_name="Цикл автоклавирования",
        related_name="items",
    )

    lot = models.ForeignKey(
        Lot,
        on_delete=models.PROTECT,
        verbose_name="Складская партия",
        related_name="autoclaving_items",
        null=True,
        blank=True,
    )

    description = models.CharField(
        "Объект обработки",
        max_length=240,
        blank=True,
        help_text=(
            "Для объекта без складской карточки укажите "
            "название и идентификационные данные. "
            "Для складской партии можно уточнить упаковку."
        ),
    )

    quantity = models.DecimalField(
        "Количество в загрузке",
        max_digits=16,
        decimal_places=4,
        validators=[MinValueValidator(Decimal("0.0001"))],
    )

    unit = models.CharField(
        "Единица количества",
        max_length=20,
        choices=[(x, x) for x in UNITS],
    )

    note = models.TextField(
        "Замечания",
        blank=True,
    )

    def clean(self):
        super().clean()

        if not self.lot_id and not self.description.strip():
            raise ValidationError({
                "description": (
                    "Выберите складскую партию "
                    "или опишите объект обработки."
                ),
            })

        if self.lot_id and self.unit != self.lot.unit:
            raise ValidationError({
                "unit": (
                    "Для складской партии используйте "
                    "её единицу учёта."
                ),
            })

    def __str__(self):
        label = self.description or str(self.lot)
        return f"{label} — {self.quantity} {self.unit}"

    class Meta:
        verbose_name = "Объект автоклавирования"
        verbose_name_plural = "Состав загрузки"
        ordering = ["pk"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name="autoclaving_item_qty_positive",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(lot__isnull=False)
                    | ~models.Q(description="")
                ),
                name="autoclaving_item_has_object",
            ),
        ]

class Report(models.Model):
    year = models.PositiveIntegerField("Год")
    quarter = models.PositiveSmallIntegerField(
        "Квартал", choices=[(i, str(i)) for i in range(1, 5)]
    )
    created_at = models.DateTimeField("Сформирован", auto_now_add=True)
    payload = models.JSONField("Сохранённый отчёт")
    approved_by = fk(USER, "Утвердил", blank=True, null=True)
    approved_at = models.DateTimeField("Утверждён", blank=True, null=True)
    sent_at = models.DateTimeField("Передан почтовому серверу", blank=True, null=True)

    def __str__(self):
        return f"{self.year} · квартал {self.quarter}"

    class Meta:
        verbose_name = "Квартальный отчёт"
        verbose_name_plural = "Квартальные отчёты"
        constraints = [
            models.UniqueConstraint(
                fields=["year", "quarter"], name="one_report_per_quarter"
            ),
            models.CheckConstraint(
                condition=Q(quarter__gte=1) & Q(quarter__lte=4),
                name="valid_report_quarter",
            ),
        ]


class Audit(Stamp):
    entity = models.CharField("Объект", max_length=120)
    object_id = models.CharField("Идентификатор", max_length=100)
    detail = models.TextField("Изменение")

    class Meta:
        verbose_name = "Событие аудита"
        verbose_name_plural = "Аудит действий"
        ordering = ["-created_at"]

