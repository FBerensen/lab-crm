import json
import re

from django import forms
from django.core.serializers.json import DjangoJSONEncoder

from lab.models import Audit, Sample, Task, lead


def can_create_task(user):
    return bool(
        user
        and user.is_active
        and user.is_staff
        and (
            lead(user)
            or user.groups.filter(name="Исполнители").exists()
        )
    )


class TaskSamplesForm(forms.ModelForm):
    sample_ranges = forms.CharField(
        label="Добавить пробы по сериям и номерам",
        required=False,
        max_length=6000,
        widget=forms.Textarea(attrs={"rows": 5}),
        help_text=(
            "Одна серия на строку. Например: QT = 1-52\n"
            "AB = 3-8 12*\n"
            "Номера разделяются пробелами. "
            "Указанные пробы добавляются к выбранным в списке. "
            "Новые карточки проб здесь не создаются."
        ),
    )

    class Meta:
        model = Task
        exclude = ("sample",)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        if "samples" in self.fields:
            self.fields["samples"].help_text = (
                "Выберите существующие пробы либо укажите "
                "серии и диапазоны ниже. Допускаются разные серии "
                "в пределах проекта задачи."
            )

    def clean(self):
        data = super().clean()
        user = getattr(self, "request_user", None)

        if not user or not can_create_task(user):
            raise forms.ValidationError(
                "Нет права создавать или изменять эту задачу."
            )

        # Existing task settings are read-only in the admin form.
        project = data.get("project")
        if project is None and self.instance.pk:
            project = self.instance.project

        if not lead(user) and not self.instance.pk:
            owner = data.get("owner")
            if owner and owner.pk != user.pk:
                self.add_error(
                    "owner",
                    "Исполнитель может создавать задачу только себе.",
                )

        if not project:
            return data

        selected = data.get("samples")
        ids = set(
            selected.values_list("pk", flat=True)
            if selected is not None else []
        )

        text = data.get("sample_ranges", "").strip()

        for line_no, line in enumerate(text.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue

            if "=" not in line:
                self.add_error(
                    "sample_ranges",
                    f"Строка {line_no}: используйте формат QT = 1-52.",
                )
                continue

            series, specification = [
                part.strip() for part in line.split("=", 1)
            ]

            if not series or not specification:
                self.add_error(
                    "sample_ranges",
                    f"Строка {line_no}: нужны серия и номера.",
                )
                continue

            numbers = set()
            invalid = False

            for token in specification.split():
                match = re.fullmatch(r"([0-9]+)-([0-9]+)", token)

                if match:
                    first, last = map(int, match.groups())
                    if not (1 <= first <= last <= 150):
                        self.add_error(
                            "sample_ranges",
                            f"Строка {line_no}: диапазон должен "
                            "находиться в пределах 1–150.",
                        )
                        invalid = True
                        break

                    numbers.update(
                        str(number)
                        for number in range(first, last + 1)
                    )
                else:
                    if (
                        len(token) > 20
                        or not re.fullmatch(r"[0-9*.,:]+", token)
                        or not re.search(r"[0-9]", token)
                        or any(
                            not 1 <= int(value) <= 150
                            for value in re.findall(r"[0-9]+", token)
                        )
                    ):
                        self.add_error(
                            "sample_ranges",
                            f"Строка {line_no}: "
                            f"недопустимый номер {token}.",
                        )
                        invalid = True
                        break
                    numbers.add(token)

            if invalid:
                continue

            found = list(
                Sample.objects.filter(
                    projects__pk=project.pk,
                    series=series,
                    number__in=numbers,
                ).values_list("pk", "number")
            )

            missing = numbers - {number for _, number in found}

            if missing:
                self.add_error(
                    "sample_ranges",
                    f"Серия {series}: в выбранном проекте "
                    "не найдены номера: "
                    + ", ".join(sorted(missing)),
                )
            else:
                ids.update(pk for pk, _ in found)

        if Sample.objects.filter(pk__in=ids).exclude(
            projects__pk=project.pk
        ).exists():
            self.add_error(
                "samples",
                "Все пробы должны относиться к проекту задачи.",
            )

        if not ids:
            self.add_error(
                "samples",
                "Выберите хотя бы одну пробу "
                "или укажите существующие пробы диапазоном.",
            )

        data["samples"] = Sample.objects.filter(pk__in=ids)
        return data


class TaskSamplesMixin:
    form = TaskSamplesForm
    exclude = ("sample",)
    filter_horizontal = ("samples",)

    def has_add_permission(self, request):
        return can_create_task(request.user)

    def has_change_permission(self, request, obj=None):
        if not can_create_task(request.user):
            return False

        if obj is None:
            return True

        if obj.status != "Назначено":
            return False

        return lead(request.user) or obj.owner_id == request.user.pk

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))

        if obj is not None:
            fields.extend([
                "project", "owner", "reviewer",
                "due", "stage", "title",
            ])

        return tuple(dict.fromkeys(fields))

    def get_form(self, request, obj=None, **kwargs):
        base = super().get_form(request, obj, **kwargs)

        class RequestBoundForm(base):
            request_user = request.user

        return RequestBoundForm

    def save_model(self, request, obj, form, change):
        # Legacy link is no longer used by the new form.
        # Existing links were copied by the data migration.
        obj.sample = None
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        task = form.instance

        before = list(
            task.samples.order_by("pk").values(
                "id", "series", "number"
            )
        )

        super().save_related(request, form, formsets, change)

        after = list(
            task.samples.order_by("pk").values(
                "id", "series", "number"
            )
        )

        if before != after:
            Audit.objects.create(
                actor=request.user,
                entity=task._meta.label,
                object_id=str(task.pk),
                detail=json.dumps(
                    {
                        "Изменение состава проб": {
                            "было": before,
                            "стало": after,
                        }
                    },
                    ensure_ascii=False,
                    cls=DjangoJSONEncoder,
                ),
            )