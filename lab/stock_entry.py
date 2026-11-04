from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect
from django.urls import path, reverse

from .models import ProductCategory, BufferName


ENTRY_TYPES = {
    "plastic": ("Пластик", "Добавить пластик"),
    "reagent": ("Реактив", "Добавить реактив"),
    "buffer": ("Буфер", "Добавить буфер"),
}


class StockEntryMixin:
    change_list_template = "admin/lab/lot/change_list.html"

    def get_urls(self):
        return [
            path(
                "add-kind/<slug:kind>/",
                self.admin_site.admin_view(self.add_kind_view),
                name="lab_lot_add_kind",
            ),
        ] + super().get_urls()

    def add_kind_view(self, request, kind):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if kind not in ENTRY_TYPES:
            raise PermissionDenied

        category_name, title = ENTRY_TYPES[kind]

        category = ProductCategory.objects.filter(
            name=category_name,
            active=True,
        ).first()

        if category is None:
            self.message_user(
                request,
                f"В справочнике отсутствует активная категория "
                f"«{category_name}». Обратитесь к администратору.",
                level=messages.ERROR,
            )
            return redirect(reverse("admin:lab_lot_changelist"))

        request.stock_entry_category = category
        return self.add_view(
            request,
            extra_context={"title": title},
        )

    def add_view(self, request, form_url="", extra_context=None):
        if not self.has_add_permission(request):
            raise PermissionDenied

        if not hasattr(request, "stock_entry_category"):
            self.message_user(
                request,
                "Выберите «Добавить пластик», "
                "«Добавить реактив» или «Добавить буфер».",
                level=messages.INFO,
            )
            return redirect(reverse("admin:lab_lot_changelist"))

        return super().add_view(
            request,
            form_url=form_url,
            extra_context=extra_context,
        )

    def get_fieldsets(self, request, obj=None):
        original = super().get_fieldsets(request, obj)

        if obj is not None:
            return original

        # Remove the category selector only from the add form.
        result = []
        for title, options in original:
            updated = dict(options)
            updated["fields"] = tuple(
                field
                for field in options["fields"]
                if field != "category"
            )
            result.append((title, updated))

        return result

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))

        if obj is not None:
            fields.append("category")

        return tuple(dict.fromkeys(fields))

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            excluded = list(kwargs.get("exclude") or ())
            excluded.append("category")
            kwargs["exclude"] = tuple(dict.fromkeys(excluded))

        base = super().get_form(request, obj, **kwargs)

        category = (
            obj.category
            if obj is not None and obj.category_id
            else getattr(request, "stock_entry_category", None)
        )

        class CategoryBoundForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)

                if obj is None and category is not None:
                    self.instance.category = category

                if (
                    category is not None
                    and category.name == "Буфер"
                    and "name" in self.fields
                ):
                    names = list(
                        BufferName.objects.filter(active=True)
                        .order_by("sort_order", "name")
                        .values_list("name", flat=True)
                    )

                    current_name = (
                        self.instance.name
                        if self.instance.pk else ""
                    )

                    # Preserve existing stock-card names.
                    if current_name and current_name not in names:
                        names.append(current_name)

                    original = self.fields["name"]

                    self.fields["name"] = forms.ChoiceField(
                        label="Наименование продукта",
                        required=True,
                        choices=[
                            ("", "Выберите буфер"),
                            *[(name, name) for name in names],
                        ],
                        initial=current_name or None,
                        help_text=(
                            "Новые варианты добавляются в справочнике "
                            "«Названия буферов». После добавления "
                            "обновите эту форму."
                        ),
                        disabled=original.disabled,
                    )

        return CategoryBoundForm

    def save_model(self, request, obj, form, change):
        if not change:
            category = getattr(request, "stock_entry_category", None)

            if category is None:
                raise PermissionDenied

            if not ProductCategory.objects.filter(
                pk=category.pk,
                active=True,
            ).exists():
                raise ValidationError(
                    "Категория стала недоступна. "
                    "Вернитесь к списку и выберите тип снова."
                )

            # Ignore any category submitted manually by the browser.
            obj.category = category

        else:
            obj.category_id = (
                type(obj).objects.values_list(
                    "category_id", flat=True
                ).get(pk=obj.pk)
            )

        # Preserve the existing receipt and stock validation logic.
        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        # Keep "Save and add another" on the same typed form.
        response = super().response_add(
            request, obj, post_url_continue
        )

        if "_addanother" in request.POST:
            response["Location"] = request.path

        return response