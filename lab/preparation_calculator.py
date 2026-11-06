from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Prefetch
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template import engines
from django.urls import reverse
from django.utils import timezone
from django.utils.html import format_html
from types import MethodType
from .models import (PreparationCalculator, PreparationProtocol, Lot, convert_preparation_quantity)

ZERO = Decimal("0")


def decimal_text(value):
    return "—" if value is None else format(value, "f")


def calculate(protocol_id, volume_ml):
    try:
        volume = Decimal(str(volume_ml))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValidationError("Укажите корректный объём в мл.") from exc
    if not volume.is_finite() or volume <= 0:
        raise ValidationError("Объём должен быть положительным конечным числом.")
    try:
        protocol = PreparationProtocol.objects.select_related("product").get(pk=protocol_id)
    except (PreparationProtocol.DoesNotExist, ValueError, TypeError) as exc:
        raise ValidationError("Протокол не найден.") from exc
    if not protocol.active or not protocol.product.active:
        raise ValidationError("Протокол или продукт отключён для новых приготовлений.")
    protocol.full_clean()
    components = list(protocol.recipe_components.order_by("position", "pk").prefetch_related(
        Prefetch("allowed_lots", queryset=Lot.objects.select_related("category", "storage_place").order_by("expiry", "pk"), to_attr="calculator_lots")))
    if not components:
        raise ValidationError("В протоколе нет компонентов.")
    rows, uses, lots_by_id = [], defaultdict(set), {}
    today = timezone.localdate()
    with localcontext() as context:
        context.prec = 50
        for component in components:
            component.protocol = protocol
            required = component.required_quantity(volume)
            candidates, available = [], ZERO
            for lot in component.calculator_lots:
                free = lot.quantity - lot.reserved
                reasons = []
                if not lot.released:
                    reasons.append("Нет допуска")
                if lot.expiry is not None and lot.expiry < today:
                    reasons.append("Срок годности истёк")
                if free <= 0:
                    reasons.append("Нет свободного остатка")
                compatible = True
                try:
                    # Compatibility is tested with one, never with zero stock.
                    convert_preparation_quantity(Decimal("1"), lot.unit, component.unit)
                except ValidationError as exc:
                    compatible = False
                    reasons.extend(exc.messages)
                contribution = ZERO
                if compatible and not reasons and free > 0:
                    contribution = convert_preparation_quantity(free, lot.unit, component.unit)
                    available += contribution
                    uses[lot.pk].add(component.pk)
                    lots_by_id[lot.pk] = lot
                candidates.append({"lot": lot, "free": free, "free_text": decimal_text(free),
                    "contribution": contribution, "contribution_text": decimal_text(contribution),
                    "reasons": reasons, "eligible": not reasons, "compatible": compatible,
                    "url": reverse("admin:lab_lot_change", args=[lot.pk])})
            shortage = max(required - available, ZERO) if required is not None else None
            rows.append({"component": component, "required": required, "required_text": decimal_text(required),
                "available": available, "available_text": decimal_text(available),
                "shortage": shortage, "shortage_text": decimal_text(shortage), "candidates": candidates,
                "no_allowed_lots": not candidates, "manual_amount": required is None})
    shared_ids = {lot_id for lot_id, ids in uses.items() if len(ids) > 1}
    for row in rows:
        row["shared"] = any(c["lot"].pk in shared_ids and c["eligible"] for c in row["candidates"])
        for candidate in row["candidates"]:
            candidate["shared"] = candidate["lot"].pk in shared_ids and candidate["eligible"]
    return {"protocol": protocol, "volume": volume, "volume_text": decimal_text(volume), "rows": rows,
        "shared_lots": [lots_by_id[key] for key in sorted(shared_ids)],
        "fixed_count": sum(not row["manual_amount"] for row in rows),
        "manual_count": sum(row["manual_amount"] for row in rows),
        "shortage_count": sum(row["shortage"] is not None and row["shortage"] > 0 for row in rows),
        "calculated_at": timezone.now()}


class CalculatorForm(forms.Form):
    protocol = forms.ModelChoiceField(label="Протокол и версия", queryset=PreparationProtocol.objects.filter(
        active=True, product__active=True).select_related("product"))
    volume_ml = forms.DecimalField(label="Требуемый конечный объём, мл", min_value=Decimal("0.0001"), max_digits=16, decimal_places=4)


PAGE = """
{% extends "admin/base_site.html" %}
{% block content %}<h1>Калькулятор приготовления</h1>
<p>Только расчёт: просмотр не создаёт приготовление, резерв, списание или запись аудита.</p>
<form method="get">{{ form.as_p }}<button name="calculate" value="1">Рассчитать</button></form>
{% if errors %}<ul class="errorlist">{% for error in errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
{% if result %}<h2>{{ result.protocol.title }} / версия {{ result.protocol.version }}</h2>
<p>Продукт: {{ result.protocol.product }}. Базовый объём: {{ result.protocol.base_volume_ml }} мл.
Расчёт на {{ result.volume_text }} мл. Время: {{ result.calculated_at }}.</p>
<p>Фиксированных компонентов: {{ result.fixed_count }}; с раздельной нехваткой: {{ result.shortage_count }};
с количеством по факту: {{ result.manual_count }}.</p>
{% if result.protocol.target_ph %}<p>Требование pH: {{ result.protocol.target_ph }}.</p>{% endif %}
{% if result.protocol.processing_notes %}<p>Особые условия: {{ result.protocol.processing_notes }}</p>{% endif %}
{% if result.shared_lots %}<div class="errornote">Общие партии: {% for lot in result.shared_lots %}{{ lot }}; {% endfor %}
Один запас доступен нескольким компонентам. Каждая строка проверена отдельно; общий подбор ещё не выполнен.
Нельзя считать весь протокол обеспеченным по этим отдельным итогам.</div>{% endif %}
{% for row in result.rows %}<fieldset><h3>{{ row.component.position }}. {{ row.component.reagent_name }}</h3>
{% if row.manual_amount %}<p>Довести до конечного объёма. Фактический расход растворителя определяется при приготовлении; это не нулевой расход.</p>
{% else %}<p>Требуется: {{ row.required_text }} {{ row.component.get_unit_display }}.
Доступно при отдельном расчёте: {{ row.available_text }} {{ row.component.get_unit_display }}.
Нехватка при отдельном расчёте: {{ row.shortage_text }} {{ row.component.get_unit_display }}.</p>{% endif %}
{% if row.component.source_concentration %}<p>Требуемая исходная концентрация: {{ row.component.source_concentration }}.</p>{% endif %}
{% if row.component.final_concentration %}<p>Конечная концентрация: {{ row.component.final_concentration }}.</p>{% endif %}
{% if row.component.note %}<p>{{ row.component.note }}</p>{% endif %}
{% if row.no_allowed_lots %}<p class="errornote">Разрешённые партии не назначены. Другие партии не подбираются автоматически.</p>{% endif %}
{% if row.shared %}<p class="errornote">Есть общие доступные партии с другими компонентами.</p>{% endif %}
<table><tr><th>Разрешённая партия</th><th>Место</th><th>Свободно в складской единице</th><th>Вклад в доступность, {{ row.component.get_unit_display }}</th><th>Состояние</th></tr>
{% for c in row.candidates %}<tr><td><a href="{{ c.url }}">{{ c.lot }}</a><br>{{ c.lot.code }}</td><td>{{ c.lot.storage_place|default:"—" }}</td>
<td>{{ c.free_text }} {{ c.lot.unit }}</td><td>{{ c.contribution_text }}</td><td>{% for reason in c.reasons %}<div>{{ reason }}</div>{% empty %}Доступна{% endfor %}{% if c.shared %}<div>Общий запас для нескольких компонентов</div>{% endif %}</td></tr>{% endfor %}</table></fieldset>{% endfor %}
<p>Расчёт Decimal с точностью 50 значащих цифр. Принудительного округления до четырёх знаков нет;
это расчёт, а не готовые количества для складской записи.</p>
<p>Свободный остаток учитывает складские резервы. Ожидающие подтверждения списания и планы без резерва его не уменьшают автоматически.
Совместимость концентраций по текстовым названиям не вычисляется; разрешённые партии задаются в рецептуре.</p>
<p><a href="{{ recipe_url }}">Открыть протокол</a> | <a href="{{ stock_url }}">Наличие и ожидающий расход</a></p>
{% endif %}{% endblock %}
"""


def staff(user):
    return bool(user and user.is_active and user.is_staff)


class CalculatorAdmin(admin.ModelAdmin):
    actions = None
    def has_module_permission(self, request): return staff(request.user)
    def has_view_permission(self, request, obj=None): return staff(request.user)
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if not staff(request.user): raise PermissionDenied
        if request.method != "GET": return HttpResponseNotAllowed(["GET"])
        return redirect(reverse("admin:lab_preparationprotocol_change", args=[object_id]))
    def changelist_view(self, request, extra_context=None):
        if not staff(request.user): raise PermissionDenied
        if request.method != "GET": return HttpResponseNotAllowed(["GET"])
        initial = {}
        value = request.GET.get("protocol", "")
        if value.isdigit():
            protocol = PreparationProtocol.objects.filter(pk=value, active=True, product__active=True).first()
            if protocol: initial = {"protocol": protocol.pk, "volume_ml": protocol.base_volume_ml}
        submitted = request.GET.get("calculate") == "1"
        form = CalculatorForm(request.GET if submitted else None, initial=initial)
        result, errors = None, []
        if submitted and form.is_valid():
            try: result = calculate(form.cleaned_data["protocol"].pk, form.cleaned_data["volume_ml"])
            except ValidationError as exc: errors = exc.messages
        context = {**self.admin_site.each_context(request), "opts": PreparationCalculator._meta,
            "title": "Калькулятор приготовления", "form": form, "result": result, "errors": errors,
            "recipe_url": reverse("admin:lab_preparationprotocol_change", args=[result["protocol"].pk]) if result else "",
            "stock_url": reverse("admin:lab_stockavailability_changelist")}
        return HttpResponse(engines["django"].from_string(PAGE).render(context, request))


def install(site):
    if not site.is_registered(PreparationCalculator): site.register(PreparationCalculator, CalculatorAdmin)
    ma = site._registry[PreparationProtocol]
    if getattr(ma, "_calculator7a_installed", False): return
    def link(self, obj):
        if obj is None or not obj.pk: return "После сохранения"
        return format_html('<a href="{}?protocol={}">Рассчитать объём и наличие</a>', reverse("admin:lab_preparationcalculator_changelist"), obj.pk)
    link.short_description = "Калькулятор"
    ma.calculator_link = MethodType(link, ma)
    ma.readonly_fields = (*ma.readonly_fields, "calculator_link")
    if ma.fields is not None: ma.fields = (*ma.fields, "calculator_link")
    ma.list_display = (*ma.list_display, "calculator_link")
    ma._calculator7a_installed = True
