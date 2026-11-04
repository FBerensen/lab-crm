from datetime import timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q, Sum
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone

from .models import BufferName, Lot, Movement, ProductCategory

ENTRY_TYPES = {
    "plastic": ("Пластик", "Добавить пластик"),
    "reagent": ("Реактив", "Добавить реактив"),
    "buffer": ("Буфер", "Добавить буфер"),
}


class StockEntryMixin:
    change_list_template = "admin/lab/lot/change_list.html"
    stock_balance_template = "admin/lab/lot/stock_balance.html"

    def get_urls(self):
        return [
            path(
                "add-kind/<slug:kind>/",
                self.admin_site.admin_view(self.add_kind_view),
                name="lab_lot_add_kind",
            ),
            path(
                "stock-balance/",
                self.admin_site.admin_view(self.stock_balance_view),
                name="lab_lot_stock_balance",
            ),
        ] + super().get_urls()

    def stock_balance_view(self, request):
        if not self.has_view_permission(request):
            raise PermissionDenied

        today = timezone.localdate()
        soon_date = today + timedelta(days=30)

        lots = list(
            Lot.objects.select_related("category", "storage_place").order_by(
                "name", "code"
            )
        )

        # Группируем движения по партиям для расчёта баланса:
        # 1) поступления, 2) расход по задачам и журналу работ,
        # 3) расход на приготовление новых реактивов/буферов, 4) прочие списания.
        movements = Movement.objects.values(
            "lot_id", "kind", "task_id", "worklog_id", "preparation_id"
        ).annotate(total_qty=Sum("qty"))

        stats_by_lot = {
            lot.pk: {
                "receipt_total": Decimal("0"),
                "used_on_tasks": Decimal("0"),
                "used_on_preparations": Decimal("0"),
                "writeoff_total": Decimal("0"),
            }
            for lot in lots
        }

        for m in movements:
            st = stats_by_lot.get(m["lot_id"])
            if st is None:
                continue
            qty = m["total_qty"] or Decimal("0")
            kind = m["kind"]
            if kind == "receipt":
                st["receipt_total"] += qty
            elif kind in ("use_reserved", "use_free"):
                if m["preparation_id"] and not m["task_id"] and not m["worklog_id"]:
                    st["used_on_preparations"] += qty
                else:
                    st["used_on_tasks"] += qty
            elif kind == "writeoff":
                st["writeoff_total"] += qty

        low_stock_lots = []
        expired_lots = []
        expiring_soon_lots = []
        rows = []

        for lot in lots:
            st = stats_by_lot[lot.pk]
            rows.append(
                {
                    "lot": lot,
                    "receipt_total": st["receipt_total"],
                    "used_on_tasks": st["used_on_tasks"],
                    "used_on_preparations": st["used_on_preparations"],
                    "writeoff_total": st["writeoff_total"],
                }
            )
            if lot.expired:
                expired_lots.append(lot)
            elif lot.expiry and today <= lot.expiry <= soon_date:
                expiring_soon_lots.append(lot)

            if lot.free < lot.minimum or lot.free <= 0:
                low_stock_lots.append(lot)

        context = {
            **self.admin_site.each_context(request),
            "title": "Наличие и баланс реактивов (склад / задачи / новые реактивы)",
            "opts": self.model._meta,
            "rows": rows,
            "low_stock_lots": low_stock_lots,
            "expired_lots": expired_lots,
            "expiring_soon_lots": expiring_soon_lots,
        }
        return TemplateResponse(request, self.stock_balance_template, context)

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

        result = []
        for title, options in original:
            updated = dict(options)
            updated["fields"] = tuple(
                field for field in options["fields"] if field != "category"
            )
            result.append((title, updated))

        return result

    def get_form(self, request, obj=None, **kwargs):
        if obj is None:
            excluded = list(kwargs.get("exclude") or ())
            excluded.append("category")
            kwargs["exclude"] = tuple(dict.fromkeys(excluded))

        base = super().get_form(request, obj, **kwargs)

        # Определяем выбранную категорию (как при добавлении, так и на странице
        # «Изменить Продукт на склад» — включая случай смены поля «Внесение» на «Буфер»).
        posted_category_id = request.POST.get("category") if request.method == "POST" else None
        posted_category = (
            ProductCategory.objects.filter(pk=posted_category_id).first()
            if posted_category_id and str(posted_category_id).isdigit()
            else None
        )

        category = (
            posted_category
            or (obj.category if obj is not None and obj.category_id else None)
            or getattr(request, "stock_entry_category", None)
        )

        class CategoryBoundForm(base):
            def __init__(self, *args, **form_kwargs):
                super().__init__(*args, **form_kwargs)

                if obj is None and category is not None:
                    self.instance.category = category

                if (
                    category is not None
                    and category.name.strip().lower() == "буфер"
                    and "name" in self.fields
                ):
                    names = list(
                        BufferName.objects.filter(active=True)
                        .order_by("sort_order", "name")
                        .values_list("name", flat=True)
                    )

                    current_name = self.instance.name if self.instance.pk else ""
                    if current_name and current_name not in names:
                        names.append(current_name)

                    original = self.fields["name"]

                    self.fields["name"] = forms.ChoiceField(
                        label="Наименование продукта",
                        required=True,
                        choices=[
                            ("", "Выберите буфер из списка"),
                            *[(name, name) for name in names],
                        ],
                        initial=current_name or None,
                        help_text=(
                            "Список буферов настраивается в справочнике "
                            "«Названия буферов» (можно добавлять и удалять позиции)."
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

            obj.category = category

        super().save_model(request, obj, form, change)

    def response_add(self, request, obj, post_url_continue=None):
        response = super().response_add(request, obj, post_url_continue)

        if "_addanother" in request.POST:
            response["Location"] = request.path

        return response