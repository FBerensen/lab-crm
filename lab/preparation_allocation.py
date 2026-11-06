from collections import defaultdict
from decimal import Decimal, localcontext
from types import MethodType
from django import forms
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, HttpResponseNotAllowed
from django.template import engines
from django.urls import path, reverse
from .models import PreparationCalculator, convert_preparation_quantity
from . import preparation_calculator as calculator

MAX_PAIRS=400
ZERO=Decimal("0")

def quantity_field(required=False):
    return forms.DecimalField(label="Количество в складской единице",required=required,
        min_value=Decimal("0.0001"),max_digits=16,decimal_places=4)

def pair_names(component_id,lot_id):
    return f"take_{component_id}_{lot_id}",f"qty_{component_id}_{lot_id}"

def validate_selection(protocol_id,volume_ml,selections):
    calculation=calculator.calculate(protocol_id,volume_ml)
    if not isinstance(selections,(list,tuple)) or len(selections)>MAX_PAIRS:
        raise ValidationError("Недопустимый размер набора.")
    components={row["component"].pk:row for row in calculation["rows"]}
    allowed={(row["component"].pk,c["lot"].pk):c for row in calculation["rows"] for c in row["candidates"]}
    per_component=defaultdict(list);per_lot={};seen=set();normalized=[]
    qty_validator=quantity_field()
    with localcontext() as context:
        context.prec=50
        for item in selections:
            if not isinstance(item,dict):raise ValidationError("Некорректная строка набора.")
            component_id,lot_id=item.get("component_id"),item.get("lot_id")
            if type(component_id) is not int or type(lot_id) is not int:raise ValidationError("Некорректные идентификаторы.")
            key=(component_id,lot_id)
            if key in seen:raise ValidationError("Пара компонент + партия повторяется.")
            seen.add(key)
            candidate=allowed.get(key)
            if candidate is None:raise ValidationError("Партия не разрешена для компонента или состав рецептуры изменился.")
            if not candidate["eligible"]:raise ValidationError("Выбранная партия сейчас недоступна: "+"; ".join(candidate["reasons"]))
            row=components[component_id];lot=candidate["lot"]
            qty=qty_validator.clean(item.get("qty"))
            if qty is None and not row["manual_amount"]:raise ValidationError("Для фиксированного компонента укажите количество.")
            converted=convert_preparation_quantity(qty,lot.unit,row["component"].unit) if qty is not None else None
            entry={"component_id":component_id,"lot_id":lot_id,"lot":lot,"qty":qty,
                "qty_text":calculator.decimal_text(qty),"converted":converted,
                "converted_text":calculator.decimal_text(converted),"manual":row["manual_amount"]}
            normalized.append(entry);per_component[component_id].append(entry)
            total=per_lot.setdefault(lot_id,{"lot":lot,"qty":ZERO,"free":candidate["free"],"unknown_count":0})
            if qty is not None:total["qty"]+=qty
            else:total["unknown_count"]+=1
        errors=[];warnings=[];rows=[];fixed_ok=True;manual_complete=True
        for original in calculation["rows"]:
            component=original["component"];chosen=per_component[component.pk]
            amount=sum((item["converted"] for item in chosen if item["converted"] is not None),ZERO)
            required=original["required"]
            if original["manual_amount"]:
                shortage=excess=None
                unknown=not chosen or any(item["qty"] is None for item in chosen)
                if unknown:
                    manual_complete=False
                    warnings.append(f"{component.reagent_name}: партия/фактическое количество растворителя ещё не определены.")
                else:warnings.append(f"{component.reagent_name}: количество введено вручную; конечный объём этим расчётом не подтверждается.")
            else:
                shortage=max(required-amount,ZERO);excess=max(amount-required,ZERO)
                if shortage>0:
                    fixed_ok=False;errors.append(f"{component.reagent_name}: не хватает {calculator.decimal_text(shortage)} {component.unit}.")
                if excess>0:
                    fixed_ok=False;errors.append(f"{component.reagent_name}: превышение {calculator.decimal_text(excess)} {component.unit}. Допуск превышения не согласован.")
            rows.append({"component":component,"required_text":calculator.decimal_text(required),"selected_text":calculator.decimal_text(amount) if not original["manual_amount"] or (chosen and all(item["qty"] is not None for item in chosen)) else "Не определено",
                "shortage_text":calculator.decimal_text(shortage),"excess_text":calculator.decimal_text(excess),
                "manual":original["manual_amount"],"chosen":chosen})
        stock_ok=True
        for total in per_lot.values():
            total["excess"]=max(total["qty"]-total["free"],ZERO)
            total["qty_text"]=calculator.decimal_text(total["qty"]);total["free_text"]=calculator.decimal_text(total["free"])
            total["excess_text"]=calculator.decimal_text(total["excess"])
            if total["excess"]>0:
                stock_ok=False;errors.append(f"Партия {total['lot'].code}: суммарно выбрано {total['qty_text']}, свободно {total['free_text']} {total['lot'].unit}.")
            if total["unknown_count"]:warnings.append(f"Партия {total['lot'].code}: есть выбранное, но ещё не указанное количество растворителя.")
    return {"calculation":calculation,"rows":rows,"totals":list(per_lot.values()),"selections":normalized,
        "errors":errors,"warnings":warnings,"fixed_valid":fixed_ok and stock_ok,
        "complete":fixed_ok and stock_ok and manual_complete,"valid":fixed_ok and stock_ok and manual_complete}


class AllocationForm(forms.Form):
    def __init__(self,calculation,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.pairs=[]
        for row in calculation["rows"]:
            for candidate in row["candidates"]:
                if candidate["eligible"]:
                    cid,lid=row["component"].pk,candidate["lot"].pk
                    take,qty=pair_names(cid,lid)
                    self.fields[take]=forms.BooleanField(label="Выбрать",required=False)
                    self.fields[qty]=quantity_field()
                    self.pairs.append((cid,lid,row["manual_amount"],take,qty))
        if len(self.pairs)>MAX_PAIRS:raise ValidationError("Слишком много разрешённых партий для одной формы. Сократите список партий рецептуры.")
    def clean(self):
        data=super().clean()
        for name in self.data:
            if str(name).startswith(("take_","qty_")) and name not in self.fields:
                raise ValidationError("Состав доступных партий изменился или передана неизвестная строка. Обновите подбор.")
            if name in self.fields and hasattr(self.data,"getlist") and len(self.data.getlist(name))>1:
                raise ValidationError("Поле количества или выбора передано несколько раз.")
        selections=[]
        for cid,lid,manual,take,qty in self.pairs:
            value=data.get(qty)
            selected=data.get(take,False) or value is not None
            if not selected:continue
            if value is None and not manual:
                self.add_error(qty,"Укажите количество из выбранной партии.")
                continue
            selections.append({"component_id":cid,"lot_id":lid,"qty":value})
        data["selections"]=selections
        return data


def input_rows(calculation,form):
    rows=[]
    for row in calculation["rows"]:
        candidates=[]
        for candidate in row["candidates"]:
            take,qty=pair_names(row["component"].pk,candidate["lot"].pk)
            candidates.append({**candidate,"take_field":form[take] if take in form.fields else None,"qty_field":form[qty] if qty in form.fields else None})
        rows.append({**row,"input_candidates":candidates})
    return rows


PAGE="""
{% extends "admin/base_site.html" %}{% block content %}<h1>Подбор партий: предпросмотр</h1>
<p>7Б не сохраняет набор, не создаёт приготовление, не резервирует и не списывает продукты.
После закрытия страницы подбор не хранится в базе. Это не подтверждение допустимости рецептурного отклонения.</p>
<form method="get">{{ parameters.as_p }}<button type="submit">Показать партии</button></form>
{% if errors %}<ul class="errorlist">{% for error in errors %}<li>{{ error }}</li>{% endfor %}</ul>{% endif %}
{% if calculation %}<h2>{{ calculation.protocol }} — {{ calculation.volume_text }} мл</h2>
<p>Для фиксированных компонентов пока требуется точное совпадение. Превышения не разрешены и не округляются автоматически.
Количество вводите в единице выбранной партии, не более четырёх знаков после запятой.</p>
<form method="post">{% csrf_token %}<input type="hidden" name="protocol" value="{{ calculation.protocol.pk }}">
<input type="hidden" name="volume_ml" value="{{ calculation.volume_text }}">{{ allocation.non_field_errors }}
{% for row in input_rows %}<fieldset><h3>{{ row.component.reagent_name }}</h3>
{% if row.manual_amount %}<p>Доведение до конечного объёма: можно выбрать партию без количества. Набор останется неполным, пока количество не определено.
Ручное количество не доказывает достижение конечного объёма.</p>{% else %}<p>Требуется {{ row.required_text }} {{ row.component.get_unit_display }}.</p>{% endif %}
<table><tr><th>Выбор</th><th>Партия</th><th>Свободно</th><th>Взять в складской единице</th><th>Состояние</th></tr>
{% for c in row.input_candidates %}<tr><td>{% if c.take_field %}{{ c.take_field }}{% else %}—{% endif %}</td>
<td>{{ c.lot }}<br>{{ c.lot.code }}</td><td>{{ c.free_text }} {{ c.lot.unit }}</td>
<td>{% if c.qty_field %}{{ c.qty_field.errors }}{{ c.qty_field }} {{ c.lot.unit }}{% else %}—{% endif %}</td>
<td>{% for reason in c.reasons %}<div>{{ reason }}</div>{% empty %}Разрешена и доступна{% endfor %}</td></tr>
{% empty %}<tr><td colspan="5">Нет разрешённых партий.</td></tr>{% endfor %}</table></fieldset>{% endfor %}
<button type="submit">Проверить весь набор</button></form>{% endif %}
{% if result %}<h2>Результат проверки</h2>
{% if result.valid %}<p>Заданные количества прошли складскую и количественную проверку. Ничего не сохранено и не зарезервировано.</p>
{% elif result.fixed_valid %}<p>Фиксированные количества проверены; набор ещё не полный из-за растворителя.</p>
{% else %}<p class="errornote">Набор не прошёл проверку.</p>{% endif %}
{% for error in result.errors %}<p class="errornote">{{ error }}</p>{% endfor %}
{% for warning in result.warnings %}<p>{{ warning }}</p>{% endfor %}
<table><tr><th>Компонент</th><th>Требуется</th><th>Выбрано в единице компонента</th><th>Недостаток</th><th>Превышение</th></tr>
{% for r in result.rows %}<tr><td>{{ r.component.reagent_name }}</td><td>{{ r.required_text }}</td><td>{{ r.selected_text }} {{ r.component.unit }}</td><td>{{ r.shortage_text }}</td><td>{{ r.excess_text }}</td></tr>{% endfor %}</table>
<h3>Общий расход каждой партии по всем компонентам</h3>
<table><tr><th>Партия</th><th>Известное выбранное количество</th><th>Свободно</th><th>Превышение остатка</th><th>Неопределённых количеств</th></tr>
{% for t in result.totals %}<tr><td>{{ t.lot }}</td><td>{{ t.qty_text }} {{ t.lot.unit }}{% if t.unknown_count %} + неопределённое количество{% endif %}</td><td>{{ t.free_text }} {{ t.lot.unit }}</td><td>{{ t.excess_text }}</td><td>{{ t.unknown_count }}</td></tr>{% endfor %}</table>{% endif %}
<p>Наличие проверяется заново при каждом запросе, но не удерживается за этим подбором.
Планы без резерва и ожидающий расход сами по себе свободный остаток не уменьшают.
Перенос в черновик и проведение будут отдельным этапом 7В с повторной проверкой.</p>
<p><a href="{{ calculator_url }}">К калькулятору 7А</a></p>{% endblock %}
"""


def allocation_view(self,request):
    if not calculator.staff(request.user):raise PermissionDenied
    if request.method not in ("GET","POST"):return HttpResponseNotAllowed(["GET","POST"])
    parameters=calculator.CalculatorForm((request.POST if request.method=="POST" else request.GET) or None)
    calculation=allocation=result=None;rows=[];errors=[]
    if parameters.is_bound and parameters.is_valid():
        try:
            pid=parameters.cleaned_data["protocol"].pk;volume=parameters.cleaned_data["volume_ml"]
            calculation=calculator.calculate(pid,volume)
            allocation=AllocationForm(calculation,request.POST if request.method=="POST" else None)
            if request.method=="POST" and allocation.is_valid():
                result=validate_selection(pid,volume,allocation.cleaned_data["selections"])
            rows=input_rows(calculation,allocation)
        except ValidationError as exc:errors=exc.messages
    return HttpResponse(engines["django"].from_string(PAGE).render({**self.admin_site.each_context(request),
        "opts":PreparationCalculator._meta,"title":"Подбор партий","parameters":parameters,"calculation":calculation,
        "allocation":allocation,"input_rows":rows,"result":result,"errors":errors,
        "calculator_url":reverse("admin:lab_preparationcalculator_changelist"),"draft7v":_draft7v_context(request,result)},request))


def install(site):
    ma=site._registry[PreparationCalculator]
    if getattr(ma,"_allocator7b_installed",False):return
    old_urls=ma.get_urls
    ma.allocation_view=MethodType(allocation_view,ma)
    def urls(self):
        return [path("allocation/",site.admin_view(self.allocation_view),name="lab_preparationcalculator_allocation")]+old_urls()
    ma.get_urls=MethodType(urls,ma)
    anchor="{% if result %}<h2>"
    replacement="""{% if result %}<p><a href="{% url 'admin:lab_preparationcalculator_allocation' %}?protocol={{ result.protocol.pk }}&volume_ml={{ result.volume_text|urlencode }}">Подобрать партии и проверить весь набор</a></p><h2>"""
    if calculator.PAGE.count(anchor)!=1:raise RuntimeError("Unexpected 7A template; cannot add allocation link safely")
    calculator.PAGE=calculator.PAGE.replace(anchor,replacement,1)
    ma._allocator7b_installed=True


def _draft7v_context(request,result):
    if result is None or not result.get("fixed_valid"):
        return None
    from .preparation_drafts import token_from_result
    try:
        return token_from_result(result,request.user)
    except ValidationError as exc:
        result["warnings"].extend(exc.messages)
        return None
