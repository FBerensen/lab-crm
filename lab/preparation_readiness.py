from datetime import timedelta
from uuid import uuid4
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import Preparation,Lot,Movement,convert_preparation_quantity
from .preparation_drafts import ensure_facts_confirmed

def positive(value):
    return value is not None and value.is_finite() and value>0

def inspect_preparation(preparation_id):
    p=Preparation.objects.select_related("recipe__product","output","output_category","output_storage_place","actor").get(pk=preparation_id)
    report={"preparation":p,"checked_at":timezone.now(),"checks":[],"totals":[],"ready":False,"processed":p.processed}
    def check(code,title,errors=None,detail=""):
        values=list(errors or [])
        report["checks"].append({"code":code,"title":title,"ok":not values,"errors":values,"detail":detail})
    if p.processed:
        report["status"]="Проведено; повторное проведение недоступно"
        report["release_status"]="Допуск зарегистрирован; актуальный складской статус смотрите в карточке партии" if p.approved_at else "Допуск по процедуре приготовления не зарегистрирован"
        return report
    check("kind","Вид приготовления",[] if p.kind=="Приготовление реактива" else ["Для обработки расходников используйте отдельный журнал автоклавирования."])
    recipe=p.recipe if p.recipe_id else None
    errors=[]
    if recipe is None:errors.append("Не выбран протокол.")
    elif not recipe.active or not recipe.product.active:errors.append("Протокол или готовый продукт неактивен.")
    check("recipe","Протокол и готовый продукт",errors,str(recipe) if recipe else "")
    errors=[]
    if not positive(p.planned_volume_ml):errors.append("Укажите положительный планируемый объём.")
    if not positive(p.actual_volume_ml):errors.append("Укажите положительный фактический объём; план не заменяет факт.")
    check("volumes","Плановый и фактический объёмы",errors)
    try:ensure_facts_confirmed(p);errors=[]
    except ValidationError as exc:errors=exc.messages
    check("confirmation","Актуальность подтверждения факта 7В",errors,"Для обычных приготовлений без подбора 7В отдельное подтверждение не требуется.")
    candidate=p.output if p.output_id else None
    errors=[];expiry=None
    if not p.output_id:
        if not p.output_category_id:errors.append("Не выбрана категория готовой партии.")
        elif not p.output_category.active:errors.append("Категория готовой партии неактивна.")
        if not p.output_storage_place_id:errors.append("Не выбрано место хранения готовой партии.")
        elif not p.output_storage_place.active:errors.append("Место хранения готовой партии неактивно.")
        expiry=p.output_expiry
        try:
            if expiry is None and recipe is not None and recipe.product.shelf_life_days:
                expiry=p.actual_day+timedelta(days=recipe.product.shelf_life_days)
            if expiry is not None:
                if expiry<p.actual_day:errors.append("Срок готовой партии раньше даты приготовления.")
                if expiry<timezone.localdate():errors.append("Срок готовой партии уже истёк.")
        except (OverflowError,TypeError):errors.append("Невозможно рассчитать срок годности; проверьте дату и справочник.")
        if not errors and recipe is not None:
            candidate=Lot(code="7G-PREVIEW-"+uuid4().hex,category=p.output_category,name=str(recipe.product),unit="мл",storage_place=p.output_storage_place,
                expiry=expiry,initial_quantity=Decimal("0"),quantity=Decimal("0"),reserved=Decimal("0"),released=False)
            try:candidate.full_clean(exclude=["code"])
            except ValidationError as exc:errors.extend(exc.messages)
    else:
        if candidate.quantity!=0 or candidate.reserved!=0:errors.append("Готовая партия должна иметь нулевые остаток и резерв.")
        if Movement.objects.filter(lot_id=candidate.pk).exists():errors.append("У готовой партии уже есть складская история; нужна отдельная новая карточка.")
    report["effective_expiry"]=expiry if not p.output_id else candidate.expiry
    check("output","Параметры будущей готовой партии",errors,"Проверка новой карточки выполняется в памяти, без save()." if not p.output_id else "Проверяется существующая карточка результата.")
    errors=[]
    if candidate is None:errors.append("Единицу результата нельзя проверить, пока не заполнены параметры готовой партии.")
    elif positive(p.actual_volume_ml):
        try:
            expected=convert_preparation_quantity(p.actual_volume_ml,"ml",candidate.unit)
            if expected!=expected.quantize(Decimal("0.0001")):errors.append("Фактический объём нельзя точно записать в единице результата до четырёх знаков; выберите более мелкую единицу.")
            # complete() resets technical qty to actual_volume_ml when it creates output.
            effective_qty=p.qty if p.output_id else p.actual_volume_ml
            if effective_qty!=expected:errors.append(f"Количество результата не соответствует объёму; требуется {expected} {candidate.unit}.")
        except (ValidationError,InvalidOperation) as exc:errors.extend(exc.messages if isinstance(exc,ValidationError) else ["Не удалось проверить точность количества."])
    elif candidate is not None:errors.append("Для проверки точности сначала укажите положительный фактический объём.")
    check("output_unit","Единица и точность количества результата",errors)
    entries=list(p.components.select_related("lot","recipe_component").order_by("pk"))
    required=set(recipe.recipe_components.values_list("pk",flat=True)) if recipe else set()
    used=set();totals={};lots={};errors=[]
    if recipe and not required:errors.append("В протоколе нет компонентов.")
    if not entries:errors.append("Не введены фактические строки расхода.")
    for c in entries:
        prefix=f"Строка №{c.pk}: "
        try:c.full_clean()
        except ValidationError as exc:errors.extend(prefix+message for message in exc.messages)
        if not c.recipe_component_id:errors.append(prefix+"не выбран компонент рецептуры.")
        elif c.recipe_component_id not in required:errors.append(prefix+"компонент другого протокола.")
        else:used.add(c.recipe_component_id)
        if c.lot_id==p.output_id:errors.append(prefix+"готовая партия не может быть собственным компонентом.")
        if c.recipe_component_id:
            try:convert_preparation_quantity(c.qty,c.lot.unit,c.recipe_component.unit)
            except ValidationError as exc:errors.extend(prefix+message for message in exc.messages)
        if positive(c.qty):
            lots[c.lot_id]=c.lot;totals[c.lot_id]=totals.get(c.lot_id,Decimal("0"))+c.qty
    for component in recipe.recipe_components.filter(pk__in=required-used).order_by("position","pk") if recipe else []:
        errors.append("Не указан фактический расход: "+component.reagent_name+" (растворитель тоже обязателен).")
    check("components","Фактические строки и полнота рецептуры",errors)
    errors=[]
    for pk in sorted(totals):
        lot=lots[pk];total=totals[pk];problems=[]
        if not lot.released:problems.append("Не допущена")
        if lot.expired:problems.append("Истёк срок годности")
        if total>lot.free:problems.append("Недостаточно свободного остатка")
        errors.extend(f"{lot}: {message}." for message in problems)
        report["totals"].append({"lot":lot,"total":total,"free":lot.free,"unit":lot.unit,"errors":problems})
    check("stock","Суммарная обеспеченность исходных партий",errors,"Расход одной партии по разным компонентам складывается; резерв не считается свободным остатком.")
    report["ready"]=all(item["ok"] for item in report["checks"])
    report["status"]="Данные готовы к проведению на момент проверки" if report["ready"] else "Есть препятствия для проведения"
    return report
