from collections import defaultdict
from decimal import Decimal
from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q, Sum
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import redirect
from django.template import engines
from django.urls import reverse
from django.utils import timezone
from .models import Lot, Movement, WorkExpenseLine, StockAvailability, ProductCategory, StoragePlace

ZERO = Decimal("0")


def active(user):
    return bool(user and user.is_active and user.is_staff)


def build_rows(lots):
    lots = list(lots)
    ids = [lot.pk for lot in lots]
    if not ids:
        return []
    free_pending = defaultdict(lambda: ZERO)
    reserved_pending = defaultdict(lambda: defaultdict(lambda: ZERO))
    demand = WorkExpenseLine.objects.filter(lot_id__in=ids, worklog__expense_request__status="pending").values(
        "lot_id", "mode", "worklog__task_id").annotate(total=Sum("qty"))
    for item in demand:
        if item["mode"] == "use_free":
            free_pending[item["lot_id"]] += item["total"]
        elif item["mode"] == "use_reserved":
            reserved_pending[item["lot_id"]][item["worklog__task_id"]] += item["total"]
    own = defaultdict(lambda: defaultdict(lambda: ZERO))
    movements = Movement.objects.filter(lot_id__in=ids, kind__in=("reserve", "release", "use_reserved")).values(
        "lot_id", "task_id", "kind").annotate(total=Sum("qty"))
    for item in movements:
        sign = 1 if item["kind"] == "reserve" else -1
        own[item["lot_id"]][item["task_id"]] += sign * item["total"]
    rows = []
    today = timezone.localdate()
    for lot in lots:
        free = lot.quantity - lot.reserved
        usable = lot.released and (lot.expiry is None or lot.expiry >= today)
        available = max(free, ZERO) if usable else ZERO
        pf = free_pending[lot.pk]
        pr = sum(reserved_pending[lot.pk].values(), ZERO)
        reserve_consistent = sum(own[lot.pk].values(), ZERO) == lot.reserved and all(q >= 0 for q in own[lot.pk].values())
        task_rows = []
        for task_id, required in reserved_pending[lot.pk].items():
            coverage = max(own[lot.pk][task_id], ZERO) if usable and reserve_consistent else ZERO
            task_rows.append({"task_id": task_id, "required": required, "own_reserve": own[lot.pk][task_id],
                              "usable_reserve": coverage, "shortage": max(required - coverage, ZERO)})
        free_shortage = max(pf - available, ZERO)
        reserve_shortage = sum((item["shortage"] for item in task_rows), ZERO)
        warnings = []
        if not lot.released:
            warnings.append("Не допущен")
        if lot.expiry is not None and lot.expiry < today:
            warnings.append("Просрочен")
        if lot.quantity == 0:
            warnings.append("Нулевой остаток")
        if available < lot.minimum:
            warnings.append("Ниже минимального свободного остатка")
        if not reserve_consistent:
            warnings.append("Требуется сверка резерва: поле и складские операции расходятся")
        if free_shortage > 0:
            warnings.append("Не хватает свободного остатка для ожидающего расхода")
        if reserve_shortage > 0:
            warnings.append("Не обеспечен резерв ожидающего расхода; проверьте задачи и допуск")
        rows.append({"lot": lot, "free": free, "available": available, "pending_free": pf,
            "pending_reserved": pr, "free_shortage": free_shortage, "reserve_shortage": reserve_shortage,
            "task_rows": task_rows, "warnings": warnings, "has_pending": pf > 0 or pr > 0,
            "has_shortage": free_shortage > 0 or reserve_shortage > 0,
            "lot_url": reverse("admin:lab_lot_change", args=[lot.pk]),
            "movement_url": reverse("admin:lab_movement_changelist") + f"?lot__id__exact={lot.pk}"})
    return rows


class Filters(forms.Form):
    q = forms.CharField(label="Поиск", required=False)
    category = forms.ModelChoiceField(label="Категория", queryset=ProductCategory.objects.all(), required=False)
    storage = forms.ModelChoiceField(label="Место хранения", queryset=StoragePlace.objects.all(), required=False)
    status = forms.ChoiceField(label="Показывать", required=False, choices=[("", "Все позиции"),
        ("problems", "С предупреждениями"), ("pending", "С ожидающим расходом"), ("shortage", "С нехваткой для ожидающего расхода")])


PAGE = """
{% extends "admin/base_site.html" %}
{% block content %}<h1>Наличие и ожидающий расход</h1>
<p>Остатки показаны на {{ today }}. Страница только для просмотра, ничего не резервирует и не списывает.</p>
<p>Доступно = свободный остаток допущенного непросроченного продукта.
Ожидающий расход ещё не списан и не зарезервирован автоматически.
Расход из резерва сравнивается отдельно с резервом каждой задачи.</p>
<p>Плановые потребности будущих задач и рецептур в этот отчёт пока не включены.</p>
<form method="get">{{ form.as_p }}<input type="submit" value="Показать"></form>
<p>Найдено позиций: {{ total }}. С предупреждениями: {{ problems }}. С ожидающим расходом: {{ pending }}.
Количества разных продуктов и единиц не суммируются.</p>
<p><a href="{{ queue_url }}">Расходы по журналу — открывайте записи для подтверждения</a></p>
<table><thead><tr><th>Продукт / место / ед.</th><th>Физически</th><th>В резерве</th><th>Свободно</th>
<th>Доступно</th><th>Минимум</th><th>Ожидает: свободный / резерв</th><th>Нехватка: свободный / резерв</th><th>Предупреждения и задачи</th></tr></thead>
<tbody>{% for row in page.object_list %}<tr>
<td><a href="{{ row.lot_url }}">{{ row.lot.name }}</a><br>{{ row.lot.code }}<br>{{ row.lot.storage_place|default:"—" }}<br>{{ row.lot.unit }}
<br><a href="{{ row.movement_url }}">Складские операции</a></td>
<td>{{ row.lot.quantity }}</td><td>{{ row.lot.reserved }}</td><td>{{ row.free }}</td><td>{{ row.available }}</td>
<td>{{ row.lot.minimum }}</td><td>{{ row.pending_free }} / {{ row.pending_reserved }}</td>
<td>{{ row.free_shortage }} / {{ row.reserve_shortage }}</td>
<td>{% for warning in row.warnings %}<div>{{ warning }}</div>{% empty %}Без предупреждений{% endfor %}
{% for item in row.task_rows %}<div>{% if item.task_id %}<a href="{% url 'admin:lab_task_change' item.task_id %}">Задача №{{ item.task_id }}</a>{% else %}Без задачи{% endif %}:
ожидает {{ item.required }}, собственный резерв {{ item.own_reserve }}, доступный резерв {{ item.usable_reserve }}, нехватка {{ item.shortage }} {{ row.lot.unit }}</div>{% endfor %}</td>
</tr>{% empty %}<tr><td colspan="9">Нет позиций по выбранным условиям.</td></tr>{% endfor %}</tbody></table>
<p>{% if page.has_previous %}<a href="?{{ query }}&page={{ page.previous_page_number }}">Назад</a>{% endif %}
Страница {{ page.number }} из {{ page.paginator.num_pages }}
{% if page.has_next %}<a href="?{{ query }}&page={{ page.next_page_number }}">Далее</a>{% endif %}</p>
{% endblock %}
"""


class AvailabilityAdmin(admin.ModelAdmin):
    actions = None
    def has_module_permission(self, request):
        return active(request.user)
    def has_view_permission(self, request, obj=None):
        return active(request.user)
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
    def change_view(self, request, object_id, form_url="", extra_context=None):
        if not active(request.user):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        return redirect(reverse("admin:lab_lot_change", args=[object_id]))
    def changelist_view(self, request, extra_context=None):
        if not active(request.user):
            raise PermissionDenied
        if request.method != "GET":
            return HttpResponseNotAllowed(["GET"])
        form = Filters(request.GET or None)
        lots = Lot.objects.select_related("category", "storage_place").order_by("name", "pk")
        status = ""
        if request.GET:
            if not form.is_valid():
                lots = lots.none()
            else:
                data = form.cleaned_data
                if data["q"]:
                    text = data["q"]
                    lots = lots.filter(Q(name__icontains=text) | Q(code__icontains=text) | Q(manufacturer__icontains=text) | Q(catalog_number__icontains=text))
                if data["category"]:
                    lots = lots.filter(category=data["category"])
                if data["storage"]:
                    lots = lots.filter(storage_place=data["storage"])
                status = data["status"]
        rows = build_rows(lots)
        if status == "problems": rows = [row for row in rows if row["warnings"]]
        if status == "pending": rows = [row for row in rows if row["has_pending"]]
        if status == "shortage": rows = [row for row in rows if row["has_shortage"]]
        page = Paginator(rows, 50).get_page(request.GET.get("page"))
        query = request.GET.copy()
        query.pop("page", None)
        context = {**self.admin_site.each_context(request), "opts": StockAvailability._meta,
            "title": "Наличие и ожидающий расход", "form": form, "page": page, "total": len(rows),
            "problems": sum(bool(row["warnings"]) for row in rows), "pending": sum(row["has_pending"] for row in rows),
            "query": query.urlencode(), "today": timezone.localdate(),
            "queue_url": reverse("admin:lab_workexpenserequest_changelist")}
        return HttpResponse(engines["django"].from_string(PAGE).render(context, request))


def install(site):
    if not site.is_registered(StockAvailability):
        site.register(StockAvailability, AvailabilityAdmin)
