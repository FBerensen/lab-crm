import hashlib
import json
from decimal import Decimal
from uuid import uuid4
from django import forms
from django.contrib.admin.widgets import AdminDateWidget
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from lab.models import (Preparation,PreparationProtocol,PreparationDraftSelection,PreparationDraftLine,
    ProductCategory,StoragePlace,Lot,convert_preparation_quantity,lead)
from lab.preparation_allocation import validate_selection
from lab.worklog6 import record

SALT="labcrm.preparation-draft.7v.v1"

def staff(user):return bool(user and user.pk and user.is_active and user.is_staff)
def can_edit(user,p):return staff(user) and not p.processed and p.kind=="Приготовление реактива" and (p.actor_id==user.pk or lead(user))
def number(value):return None if value is None else format(Decimal(value).normalize(),"f")
def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode("utf-8")).hexdigest()

def recipe_state(protocol):
    product=protocol.product
    return {"id":protocol.pk,"title":protocol.title,"version":protocol.version,"base_volume_ml":number(protocol.base_volume_ml),
        "instructions":protocol.instructions,"target_ph":protocol.target_ph,"processing_notes":protocol.processing_notes,
        "product":{"id":product.pk,"name":product.name,"kind":product.kind,"concentration":product.concentration,
            "storage_conditions":product.storage_conditions,"shelf_life_days":product.shelf_life_days,"requires_autoclaving":product.requires_autoclaving},
        "components":[{"id":c.pk,"name":c.reagent_name,"position":c.position,"quantity":number(c.quantity),"unit":c.unit,
            "to_final_volume":c.to_final_volume,"source_concentration":c.source_concentration,"final_concentration":c.final_concentration,
            "note":c.note,"allowed_lots":sorted(c.allowed_lots.values_list("pk",flat=True))}
            for c in protocol.recipe_components.order_by("position","pk")]}

def source_state(result):
    return {"recipe":recipe_state(result["calculation"]["protocol"]),"volume_ml":number(result["calculation"]["volume"]),
        "selections":[{"component_id":r["component_id"],"lot_id":r["lot_id"],"qty":number(r["qty"]),"unit":r["lot"].unit,
                       "lot_code":r["lot"].code,"manual":r["manual"]}
                      for r in sorted(result["selections"],key=lambda r:(r["component_id"],r["lot_id"]))]}

def token_from_result(result,user):
    if not staff(user):raise PermissionDenied
    if not result["fixed_valid"]:raise ValidationError("Сначала исправьте подбор фиксированных компонентов и общий расход партий.")
    source=source_state(result)
    payload={"version":1,"user":user.pk,"request_key":str(uuid4()),"source":source,"source_digest":digest(source)}
    token=signing.dumps(payload,salt=SALT,compress=True)
    if len(token)>1000000:raise ValidationError("Снимок слишком большой; сократите описание или подбор.")
    return token

def issue_token(protocol_id,volume_ml,selections,user):return token_from_result(validate_selection(protocol_id,volume_ml,selections),user)

def unseal(token,user):
    if not staff(user):raise PermissionDenied
    try:payload=signing.loads(token,salt=SALT,max_age=1800)
    except signing.BadSignature as exc:raise ValidationError("Подбор устарел или повреждён; повторите 7Б.") from exc
    if not isinstance(payload,dict):raise ValidationError("Некорректный снимок подбора.")
    if payload.get("version")!=1 or payload.get("user")!=user.pk:raise PermissionDenied
    if digest(payload["source"])!=payload.get("source_digest"):raise ValidationError("Повреждён снимок подбора.")
    return payload

def revalidate(payload):
    source=payload["source"]
    rows=[{"component_id":r["component_id"],"lot_id":r["lot_id"],"qty":r["qty"]} for r in source["selections"]]
    result=validate_selection(source["recipe"]["id"],source["volume_ml"],rows)
    if not result["fixed_valid"]:raise ValidationError(result["errors"] or ["Подбор больше не действителен."])
    if digest(source_state(result))!=payload["source_digest"]:raise ValidationError("Рецептура, партии или единицы изменились. Повторите 7Б.")
    return result

class MetadataForm(forms.Form):
    output_category=forms.ModelChoiceField(label="Категория будущей партии",queryset=ProductCategory.objects.filter(active=True))
    output_storage_place=forms.ModelChoiceField(label="Место будущей партии",queryset=StoragePlace.objects.filter(active=True))
    output_expiry=forms.DateField(label="Срок годности (необязательно)",required=False,widget=AdminDateWidget(format="%d.%m.%Y"),input_formats=["%d.%m.%Y","%Y-%m-%d"])
    note=forms.CharField(label="Примечание к черновику",required=False,max_length=10000,widget=forms.Textarea)
    def clean_output_expiry(self):
        value=self.cleaned_data["output_expiry"]
        if value and value<timezone.localdate():raise ValidationError("Срок годности уже истёк.")
        return value

def metadata_state(data):
    return {"category":data["output_category"].pk,"storage":data["output_storage_place"].pk,
        "expiry":data["output_expiry"].isoformat() if data["output_expiry"] else None,"note":data["note"]}

def existing_request(payload,user,request_digest):
    existing=PreparationDraftSelection.objects.select_related("preparation").filter(request_key=payload["request_key"]).first()
    if existing:
        if existing.created_by_id!=user.pk:raise PermissionDenied
        if existing.request_digest!=request_digest:raise ValidationError("Этот ключ создания уже использован с другими параметрами.")
        return existing.preparation
    return None

@transaction.atomic
def create_draft(token,user,metadata):
    payload=unseal(token,user)
    form=MetadataForm(metadata)
    if not form.is_valid():raise ValidationError([f"{name}: {'; '.join(errors)}" for name,errors in form.errors.items()])
    data=form.cleaned_data;meta=metadata_state(data)
    request_digest=digest({"source_digest":payload["source_digest"],"metadata":meta})
    old=existing_request(payload,user,request_digest)
    if old:return old,False
    protocol=PreparationProtocol.objects.select_for_update(no_key=True).get(pk=payload["source"]["recipe"]["id"])
    old=existing_request(payload,user,request_digest)
    if old:return old,False
    category=ProductCategory.objects.select_for_update(no_key=True).get(pk=data["output_category"].pk)
    storage=StoragePlace.objects.select_for_update(no_key=True).get(pk=data["output_storage_place"].pk)
    if not category.active or not storage.active:raise ValidationError("Категория или место хранения отключены.")
    lot_ids={r["lot_id"] for r in payload["source"]["selections"]}
    list(Lot.objects.select_for_update().filter(pk__in=lot_ids).order_by("pk"))
    result=revalidate(payload)
    volume=Decimal(payload["source"]["volume_ml"])
    p=Preparation(actor=user,actual_day=timezone.localdate(),kind="Приготовление реактива",recipe=protocol,
        protocol=str(protocol)[:240],qty=volume,planned_volume_ml=volume,actual_volume_ml=None,
        output_category=category,output_storage_place=storage,output_expiry=data["output_expiry"],
        note=("Черновик 7В: подбор сохранён как план, фактический расход ещё не подтверждён.\n"+data["note"]).strip())
    p.full_clean();p.save()
    selection=PreparationDraftSelection(preparation=p,protocol=protocol,request_key=payload["request_key"],planned_volume_ml=volume,
        source_digest=payload["source_digest"],request_digest=request_digest,snapshot={"source":payload["source"],"metadata":meta},created_by=user)
    selection.full_clean();selection.save()
    for entry in result["selections"]:
        line=PreparationDraftLine(selection=selection,recipe_component_id=entry["component_id"],lot_id=entry["lot_id"],
            planned_qty=entry["qty"],unit_snapshot=entry["lot"].unit,manual_amount=entry["manual"])
        line.full_clean();line.save()
    record(user,p,{"действие":"Создание черновика из 7Б без списания","подбор":selection.pk,"плановый_объём":volume,"факт_не_заполнен":True})
    record(user,selection,{"действие":"Сохранён план подбора","source_digest":selection.source_digest})
    return p,True


def fact_snapshot(p):
    if p.actual_volume_ml is None or not p.actual_volume_ml.is_finite() or p.actual_volume_ml<=0:raise ValidationError("Сначала укажите фактический конечный объём в карточке.")
    if p.qty!=p.actual_volume_ml and not p.output_id:raise ValidationError("Сохраните карточку: служебное количество не соответствует фактическому объёму.")
    if p.actual_day>timezone.localdate():raise ValidationError("Дата выполнения не может быть будущей.")
    if not p.recipe_id:raise ValidationError("Не указан протокол.")
    if not p.recipe.active or not p.recipe.product.active:raise ValidationError("Рецептура или продукт отключены.")
    if p.planned_volume_ml is None or not p.planned_volume_ml.is_finite() or p.planned_volume_ml<=0:raise ValidationError("Не указан корректный планируемый объём.")
    if not p.output_category_id or not p.output_storage_place_id:raise ValidationError("Укажите категорию и место будущей партии.")
    if not p.output_category.active or not p.output_storage_place.active:raise ValidationError("Категория или место будущей партии отключены.")
    if p.output_expiry and (p.output_expiry<p.actual_day or p.output_expiry<timezone.localdate()):raise ValidationError("Некорректный срок годности будущей партии.")
    entries=list(p.components.select_related("lot","recipe_component").order_by("pk"))
    required=set(p.recipe.recipe_components.values_list("pk",flat=True));used=set();rows=[]
    if not entries:raise ValidationError("Введите фактический расход в строках исходных партий.")
    for c in entries:
        c.full_clean()
        if c.recipe_component_id not in required:raise ValidationError("Фактическая строка не соответствует рецептуре.")
        if c.lot_id==p.output_id:raise ValidationError("Готовая партия не может быть компонентом.")
        convert_preparation_quantity(c.qty,c.lot.unit,c.recipe_component.unit)
        used.add(c.recipe_component_id)
        rows.append({"id":c.pk,"component":c.recipe_component_id,"lot":c.lot_id,"qty":number(c.qty),"unit":c.lot.unit})
    if required-used:raise ValidationError("Укажите факт для каждого компонента, включая растворитель.")
    return {"recipe":recipe_state(p.recipe),"actual_volume_ml":number(p.actual_volume_ml),"actual_day":p.actual_day.isoformat(),
        "qty":number(p.qty),"planned_volume_ml":number(p.planned_volume_ml),"output_category":p.output_category_id,
        "output_storage":p.output_storage_place_id,"output_expiry":p.output_expiry.isoformat() if p.output_expiry else None,"components":rows}

@transaction.atomic
def confirm_facts(preparation_id,user,*,confirmed=False,expected_digest=None):
    p=Preparation.objects.select_for_update().get(pk=preparation_id)
    if not can_edit(user,p):raise PermissionDenied
    if not confirmed:raise ValidationError("Нужно явное подтверждение фактических данных.")
    selection=PreparationDraftSelection.objects.select_for_update().get(preparation=p)
    state=fact_snapshot(p)
    if expected_digest!=digest(state):raise ValidationError("Фактические данные изменились после просмотра; обновите страницу подтверждения.")
    if state["recipe"]!=selection.snapshot["source"]["recipe"]:raise ValidationError("Рецептура изменилась после создания подбора. Нужна отдельная проверка.")
    if p.recipe_id!=selection.protocol_id or p.planned_volume_ml!=selection.planned_volume_ml:raise ValidationError("План и карточка расходятся.")
    ids={row["lot"] for row in state["components"]}
    lots={lot.pk:lot for lot in Lot.objects.select_for_update().filter(pk__in=ids).order_by("pk")}
    totals={}
    for row in state["components"]:totals[row["lot"]]=totals.get(row["lot"],Decimal("0"))+Decimal(row["qty"])
    for pk,qty in totals.items():
        lot=lots[pk]
        if not lot.released or lot.expired or qty>lot.free:raise ValidationError("Фактический расход сейчас не обеспечен доступным свободным остатком.")
    value=digest(state)
    if selection.facts_digest==value and selection.facts_confirmed_by_id:return False
    before=selection.facts_digest
    selection.facts_digest=value;selection.facts_snapshot=state
    selection.facts_confirmed_by=user;selection.facts_confirmed_at=timezone.now()
    selection.full_clean();selection.save(update_fields=["facts_digest","facts_snapshot","facts_confirmed_by","facts_confirmed_at"])
    record(user,p,{"действие":"Подтверждены фактические данные без списания","было":before,"отпечаток":value,"факт":state})
    return True

def ensure_facts_confirmed(p):
    if p.processed:return
    selection=PreparationDraftSelection.objects.filter(preparation_id=p.pk).first()
    if selection is None:return
    if not selection.facts_confirmed_by_id or not selection.facts_digest:raise ValidationError("Черновик 7В: сначала введите и подтвердите фактические данные.")
    if p.recipe_id!=selection.protocol_id or p.planned_volume_ml!=selection.planned_volume_ml:raise ValidationError("План черновика изменён.")
    state=fact_snapshot(p)
    if state["recipe"]!=selection.snapshot["source"]["recipe"] or digest(state)!=selection.facts_digest:
        raise ValidationError("Факт или рецептура изменены после подтверждения. Повторите проверку факта.")
