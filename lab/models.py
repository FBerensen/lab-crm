import re
from decimal import Decimal
from uuid import uuid4

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

USER = settings.AUTH_USER_MODEL

# Единицы измерения (п. 9 ТЗ)
UNITS = ["л", "мл", "мкл", "кг", "г", "мкг", "шт", "упак"]

STAGES = [
    "Получение материала",
    "Фиксация материала",
    "Выделение ДНК/РНК",
    "Создание блоков",
    "Постановка ПЦР",
    "Real-Time ПЦР",
    "Фрагментный анализ (ФА)",
    "Секвенирование",
    "Маркерный анализ",
    "Анализ результатов",
    "Проверка результатов",
]

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

MOVE_TYPES = [
    ("receipt", "Поступление"),
    ("reserve", "Резервирование"),
    ("release", "Снятие резерва"),
    ("use_reserved", "Расход из резерва"),
    ("use_free", "Расход свободного остатка"),
    ("writeoff", "Списание свободного остатка"),
]


def validate_sample_series(value):
    if not value or not (2 <= len(value) <= 5):
        raise ValidationError("Серия должна содержать от 2 до 5 символов.")


def validate_sample_number(value):
    if not value:
        raise ValidationError("Укажите номер.")
    if not re.fullmatch(r"[0-9*.,:]+", value):
        raise ValidationError(
            "Номер может содержать только цифры 1–150 и символы * , . .. :"
        )
    numbers = re.findall(r"\d+", value)
    if not numbers:
        raise ValidationError("Номер должен содержать хотя бы одно число от 1 до 150.")
    for n in numbers:
        if not (1 <= int(n) <= 150):
            raise ValidationError(f"Число {n} вне диапазона 1–150.")


def lead(user):
    return bool(
        user
        and user.is_active
        and user.is_staff
        and (
            user.is_superuser
            or user.groups.filter(name="Руководители").exists()
        )
    )


def warehouse(user):
    return lead(user) or bool(
        user
        and user.is_active
        and user.is_staff
        and user.groups.filter(name="Склад").exists()
    )


def fk(model, label, **kwargs):
    return models.ForeignKey(
        model,
        on_delete=models.PROTECT,
        verbose_name=label,
        **kwargs,
    )


def positive():
    return models.DecimalField(
        "Количество",
        max_digits=16,
        decimal_places=4,
        validators=[
            MinValueValidator(Decimal("0.0001")),
            MaxValueValidator(Decimal("100000")),
        ],
    )


def preparation_qty_field():
    return models.DecimalField(
        "Количество",
        max_digits=16,
        decimal_places=4,
        validators=[
            MinValueValidator(Decimal("0.1")),
            MaxValueValidator(Decimal("100000")),
        ],
        help_text="Допустимый диапазон: от 0,1 до 100000.",
    )


def new_stock_code():
    return "STK-" + uuid4().hex.upper()


class Stamp(models.Model):
    actor = fk(USER, "Кто внёс запись")
    created_at = models.DateTimeField("Когда внесено", auto_now_add=True)

    class Meta:
        abstract = True


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


class BufferName(models.Model):
    name = models.CharField("Название буфера", max_length=240, unique=True)
    active = models.BooleanField("Активен", default=True)
    sort_order = models.PositiveIntegerField("Порядок", default=100)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Название буфера"
        verbose_name_plural = "Названия буферов"
        ordering = ["sort_order", "name"]


class Project(models.Model):
    title = models.CharField("Название", max_length=240)
    kind = models.CharField(
        "Тип",
        max_length=40,
        choices=[
            (x, x)
            for x in ("Госзадание", "Проект", "Аспирантская работа")
        ],
    )
    manager = fk(USER, "Руководитель")
    due = models.DateField("Срок", blank=True, null=True)
    description = models.TextField("Описание", blank=True)

    def __str__(self):
        return self.title

    class Meta:
        verbose_name = "Проект"
        verbose_name_plural = "Проекты"


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
            name = (
                type(self)
                .objects.values_list("name", flat=True)
                .get(pk=self.pk)
            )
            Lot.objects.filter(storage_place_id=self.pk).update(location=name)
            Sample.objects.filter(storage_place_id=self.pk).update(location=name)

    def __str__(self):
        return self.name

    class Meta:
        verbose_name = "Место хранения"
        verbose_name_plural = "Места хранения"
        ordering = ["sort_order", "name"]


class Sample(models.Model):
    series = models.CharField(
        "Серия",
        max_length=5,
        validators=[validate_sample_series],
        help_text="От 2 до 5 символов (уникальный код серии проб).",
    )
    number = models.CharField(
        "Номер",
        max_length=20,
        validators=[validate_sample_number],
        help_text="Числа от 1 до 150, допускаются символы * , . .. :",
    )
    project = fk(Project, "Проект")
    parent = fk("self", "Исходный образец/проба", blank=True, null=True)
    kind = models.CharField(
        "Тип материала",
        max_length=60,
        choices=[
            (x, x)
            for x in (
                "Коллекционный образец",
                "Растение",
                "Тканевая проба",
                "ДНК",
                "РНК",
                "Аликвота",
                "Другое",
            )
        ],
    )
    storage_place = fk(
        StoragePlace,
        "Место хранения (из справочника)",
        blank=True,
        null=True,
        related_name="samples",
    )
    location = models.CharField(
        "Место хранения",
        max_length=240,
        blank=True,
        help_text="Заполняется автоматически при выборе места из справочника либо вручную.",
    )
    note = models.TextField("Примечание", blank=True)

    @property
    def code(self):
        return f"{self.series} №{self.number}"

    @property
    def series_and_number(self):
        return self.code

    def clean(self):
        super().clean()
        if self.storage_place_id and not self.location:
            self.location = self.storage_place.name
        elif self.location and not self.storage_place_id:
            matched_place = StoragePlace.objects.filter(
                name=self.location.strip()
            ).first()
            if matched_place:
                self.storage_place = matched_place
        elif not self.storage_place_id and not self.location.strip():
            raise ValidationError(
                "Выберите место хранения из справочника или укажите его текстом."
            )

        if self.parent_id:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError("Образец не может ссылаться на себя.")
            if self.project_id and self.parent.project_id != self.project_id:
                raise ValidationError(
                    "Производная проба должна относиться "
                    "к тому же проекту, что и исходная."
                )

    def save(self, *args, **kwargs):
        if self.storage_place_id:
            self.location = self.storage_place.name
        elif self.location:
            matched_place = StoragePlace.objects.filter(
                name=self.location.strip()
            ).first()
            if matched_place:
                self.storage_place = matched_place
        super().save(*args, **kwargs)

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
        "Этап",
        max_length=80,
        choices=[(x, x) for x in STAGES],
        default=STAGES[0],
    )
    status = models.CharField(
        "Состояние",
        max_length=30,
        choices=[
            (x, x)
            for x in (
                "Назначено",
                "В работе",
                "Остановлено",
                "На проверке",
                "Завершено",
            )
        ],
        default="Назначено",
    )
    parent = fk("self", "Повтор задачи", blank=True, null=True)
    result = models.TextField("Текущий результат", blank=True)
    blocker = models.TextField("Причина остановки", blank=True)
    next_action = models.TextField("Следующее действие", blank=True)
    next_actor = fk(
        USER,
        "Кто устраняет препятствие",
        related_name="lab_blockers",
        blank=True,
        null=True,
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
        if self.sample_id and self.project_id and self.sample.project_id != self.project_id:
            raise ValidationError("Проба относится к другому проекту.")

    def __str__(self):
        return f"#{self.pk} · {self.title}"

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"


class TaskEvent(Stamp):
    task = fk(Task, "Задача")
    action = models.CharField("Действие", max_length=20, choices=EVENTS)
    note = models.TextField("Результат / причина / комментарий", blank=True)
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
    next_action = models.TextField("Следующее действие", blank=True)
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
    correction_of = fk("self", "Исправление записи", blank=True, null=True)

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


class Lot(models.Model):
    code = models.CharField(
        "Внутренний код",
        max_length=100,
        unique=True,
        default=new_stock_code,
        editable=False,
    )
    category = fk(
        ProductCategory,
        "Внесение",
        blank=True,
        null=True,
        related_name="products",
    )
    name = models.CharField("Наименование продукта", max_length=240)
    manufacturer = models.CharField("Изготовитель", max_length=240, blank=True)
    catalog_number = models.CharField("Кат. номер", max_length=120, blank=True)
    unit = models.CharField(
        "Единица учёта",
        max_length=20,
        choices=[(x, x) for x in UNITS],
    )
    storage_place = fk(
        StoragePlace,
        "Место хранения",
        blank=True,
        null=True,
        related_name="products",
    )
    location = models.CharField(
        "Прежнее место хранения",
        max_length=240,
        blank=True,
        editable=False,
    )
    initial_quantity = models.DecimalField(
        "Количество",
        max_digits=16,
        decimal_places=4,
        blank=True,
        null=True,
        validators=[
            MinValueValidator(Decimal("0")),
            MaxValueValidator(Decimal("100000")),
        ],
        help_text=(
            "Количество при создании карточки, в выбранной единице учёта. "
            "Будет проведено поступлением один раз. Для новой карточки "
            "приготовления или автоклавирования укажите 0."
        ),
    )
    expiry = models.DateField("Годен до", null=True, blank=True)
    minimum = models.DecimalField(
        "Минимальный свободный остаток",
        max_digits=16,
        decimal_places=4,
        default=0,
        validators=[MinValueValidator(0)],
    )
    quantity = models.DecimalField(
        "Физический остаток",
        max_digits=16,
        decimal_places=4,
        default=0,
    )
    reserved = models.DecimalField(
        "В резерве",
        max_digits=16,
        decimal_places=4,
        default=0,
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
        return f"{self.name} · {self.code} [{self.unit}]"

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
                condition=Q(initial_quantity__isnull=True)
                | Q(initial_quantity__gte=0),
                name="lot_initial_quantity_nonnegative",
            ),
        ]


class Movement(Dated):
    lot = fk(Lot, "Продукт")
    kind = models.CharField("Операция", max_length=20, choices=MOVE_TYPES)
    qty = positive()
    task = fk(Task, "Задача", null=True, blank=True, related_name="movements")
    worklog = fk(
        WorkLog,
        "Запись журнала работ",
        null=True,
        blank=True,
        related_name="movements",
    )
    preparation = fk(
        "Preparation",
        "Общелабораторная работа",
        null=True,
        blank=True,
        related_name="movements",
    )
    note = models.TextField("Основание / комментарий")

    def clean(self):
        super().clean()
        if self.worklog_id and not self.task_id and self.worklog.task_id:
            self.task_id = self.worklog.task_id
        if (
            self.kind in ("reserve", "release", "use_reserved")
            and not self.task_id
        ):
            raise ValidationError("Для работы с резервом выберите задачу.")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Складские операции нельзя перезаписывать.")

        with transaction.atomic():
            if self.worklog_id and not self.task_id and self.worklog.task_id:
                self.task_id = self.worklog.task_id
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
        return f"{self.lot.code} · {self.get_kind_display()} · {self.qty} {self.lot.unit}"

    class Meta:
        verbose_name = "Складская операция"
        verbose_name_plural = "Поступления, резервы и расход"
        ordering = ["-created_at"]


class Preparation(Dated):
    kind = models.CharField(
        "Тип задачи",
        max_length=40,
        choices=[
            (x, x)
            for x in (
                "Приготовление реактива",
                "Приготовление буфера",
                "Автоклавирование расходников",
            )
        ],
    )
    output = models.OneToOneField(
        Lot,
        on_delete=models.PROTECT,
        verbose_name="Добавить реактив",
        related_name="production_record",
    )
    qty = preparation_qty_field()
    unit = models.CharField(
        "Единица измерения",
        max_length=20,
        choices=[(x, x) for x in UNITS],
        default="мл",
    )
    protocol = models.CharField(
        "СОП / протокол / идентификатор цикла", max_length=240
    )
    note = models.TextField("Результат и замечания", blank=True)
    processed = models.BooleanField("Проведено по складу", default=False)
    approved_by = fk(
        USER,
        "Кто допустил партию",
        blank=True,
        null=True,
        related_name="+",
    )
    approved_at = models.DateTimeField("Дата допуска", null=True, blank=True)

    def complete(self, require_approval=False):
        with transaction.atomic():
            p = Preparation.objects.select_for_update().get(pk=self.pk)
            if p.processed:
                raise ValidationError("Партия уже проведена по складу.")

            components = list(p.components.all())
            if not components:
                raise ValidationError("Добавьте исходные партии.")

            ids = sorted({p.output_id, *[c.lot_id for c in components]})
            locked = {
                x.pk: x
                for x in Lot.objects.select_for_update()
                .filter(pk__in=ids)
                .order_by("pk")
            }
            output = locked[p.output_id]

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

            if p.unit and output.unit != p.unit:
                output.unit = p.unit
            output.released = not require_approval
            output.save(update_fields=["unit", "released"])

            Movement.objects.create(
                actor=p.actor,
                actual_day=p.actual_day,
                lot=output,
                kind="receipt",
                qty=p.qty,
                preparation=p,
                note=f"Получено ({p.kind}): {p.protocol}",
            )
            Preparation.objects.filter(pk=p.pk).update(processed=True)
            self.processed = True

    def __str__(self):
        return f"{self.kind} · {self.output.code}"

    class Meta:
        verbose_name = "Общелабораторная работа"
        verbose_name_plural = "Приготовление и автоклавирование"


class Component(models.Model):
    preparation = fk(Preparation, "Общелабораторная работа", related_name="components")
    lot = fk(Lot, "Исходная партия")
    qty = positive()
    unit = models.CharField(
        "Единица измерения",
        max_length=20,
        choices=[(x, x) for x in UNITS],
        blank=True,
        default="",
    )

    def save(self, *args, **kwargs):
        if self.lot_id and not self.unit:
            self.unit = self.lot.unit
        super().save(*args, **kwargs)

    class Meta:
        verbose_name = "Исходная партия"
        verbose_name_plural = "Исходные партии и фактический расход"


class Report(models.Model):
    year = models.PositiveIntegerField("Год")
    quarter = models.PositiveSmallIntegerField(
        "Квартал", choices=[(i, str(i)) for i in range(1, 5)]
    )
    created_at = models.DateTimeField("Сформирован", auto_now_add=True)
    payload = models.JSONField("Сохранённый отчёт")
    approved_by = fk(USER, "Утвердил", blank=True, null=True)
    approved_at = models.DateTimeField("Утверждён", blank=True, null=True)
    sent_at = models.DateTimeField(
        "Передан почтовому серверу", blank=True, null=True
    )

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
