from decimal import Decimal
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from lab.models import SourceBlock, SourceBlockWell, Sample
from lab.source_blocks8v_models import ROWS
from lab.sample_batch import can_edit_samples
from lab.worklog6 import record

UNSET=object()

def authorise(user):
    if not (user and user.is_active and user.is_staff and can_edit_samples(user)):raise PermissionDenied

def check_revision(block,expected):
    if isinstance(expected,bool) or not isinstance(expected,int) or expected!=block.revision:
        raise ValidationError("Блок изменился после открытия страницы. Обновите страницу и повторите ввод.")

def block_snapshot(block):
    return {"название":block.name,"ДНК_мкл":str(block.default_dna_ul),"вода_мкл":str(block.default_water_ul),"примечание":block.note,"версия":block.revision}

def entry(well):
    return {"row":well.row,"column":well.column,"kind":well.kind,"sample_id":well.sample_id,
        "sample_code":well.sample.code if well.sample_id else None,"label":well.raw_label,
        "dna":format(well.dna_ul,"f"),"water":format(well.water_ul,"f") if well.water_ul is not None else None,"note":well.note}

def equal_entries(a,b):
    if a is None or b is None:return a is b
    for key in ("row","column","kind","sample_id","label","note"):
        if a[key]!=b[key]:return False
    return Decimal(a["dna"])==Decimal(b["dna"]) and ((a["water"] is None and b["water"] is None) or
        (a["water"] is not None and b["water"] is not None and Decimal(a["water"])==Decimal(b["water"])))

def well_snapshot(well):
    return {"позиция":well.position,"тип":well.kind,"образец_id":well.sample_id,"проба":well.display_label,
        "ДНК_мкл":str(well.dna_ul),"вода_мкл":str(well.water_ul) if well.water_ul is not None else None,"примечание":well.note}

@transaction.atomic
def save_block(block,user,expected_revision):
    authorise(user);adding=block._state.adding;before=None
    if adding:
        if block.pk is not None or isinstance(expected_revision,bool) or expected_revision!=0:raise ValidationError("Некорректная версия нового блока.")
        block.created_by=user;block.revision=0
    else:
        current=SourceBlock.objects.select_for_update().get(pk=block.pk);check_revision(current,expected_revision)
        before=block_snapshot(current);block.created_by_id=current.created_by_id;block.created_at=current.created_at;block.revision=current.revision+1
    block.updated_by=user;block.full_clean();block.save()
    record(user,block,{"действие":"Создание исходного блока" if adding else "Изменение исходного блока","было":before,"стало":block_snapshot(block)})
    return block

def locked_block(block_id,expected_revision):
    block=SourceBlock.objects.select_for_update().get(pk=block_id);check_revision(block,expected_revision);return block

def position(row,column):
    if row not in ROWS or isinstance(column,bool) or not isinstance(column,int) or not 1<=column<=12:raise ValidationError("Допустимы позиции A1–H12.")

def advance(block,user):
    block.revision+=1;block.updated_by=user;block.save(update_fields=["revision","updated_by","updated_at"])

def store_entry(block,data,user):
    position(data["row"],data["column"])
    well=SourceBlockWell.objects.filter(block=block,row=data["row"],column=data["column"]).first()
    if well is None:well=SourceBlockWell(block=block,row=data["row"],column=data["column"],created_by=user)
    well.kind=data["kind"];well.sample_id=data["sample_id"];well.raw_label=data["label"]
    well.dna_ul=data["dna"];well.water_ul=data["water"];well.note=data["note"];well.updated_by=user
    well.full_clean();well.save();return well

@transaction.atomic
def set_position(block_id,row,column,user,expected_revision,*,kind="sample",sample_id=None,label="",dna_ul=UNSET,water_ul=UNSET,note=""):
    authorise(user);position(row,column);block=locked_block(block_id,expected_revision)
    old=SourceBlockWell.objects.select_related("sample").filter(block=block,row=row,column=column).first()
    before=well_snapshot(old) if old else None
    if kind not in SourceBlockWell.Kind.values:raise ValidationError("Неизвестный тип позиции.")
    if kind=="sample":
        if label:raise ValidationError("У зарегистрированной пробы не должно быть свободной подписи.")
        try:sample=Sample.objects.get(pk=sample_id)
        except (Sample.DoesNotExist,TypeError,ValueError):raise ValidationError("Проба не найдена. Выберите существующую пробу.")
    else:
        if sample_id is not None:raise ValidationError("Контроль и незарегистрированная позиция не связаны с карточкой пробы.")
        sample=None
    if kind=="water":
        if label:raise ValidationError("Для контроля свободная подпись не задаётся.")
        if dna_ul is not UNSET and dna_ul is not None:
            try:zero=Decimal(str(dna_ul))==0
            except Exception:zero=False
            if not zero:raise ValidationError("Водный контроль содержит 0 мкл ДНК.")
        dna=Decimal(0)
        water=(old.water_ul if old and old.kind==kind else None) if water_ul is UNSET else water_ul
    else:
        dna=(old.dna_ul if old else block.default_dna_ul) if dna_ul is UNSET else dna_ul
        water=(old.water_ul if old and old.water_ul is not None else block.default_water_ul) if water_ul is UNSET else water_ul
    data={"row":row,"column":column,"kind":kind,"sample_id":sample.pk if sample else None,"label":label,
        "dna":dna,"water":water,"note":note}
    well=store_entry(block,data,user);advance(block,user)
    record(user,block,{"действие":"Заполнение лунки" if before is None else "Изменение лунки","было":before,"стало":well_snapshot(well),"версия":block.revision})
    return well

# Compatibility: existing 8V callers keep their signature and None-as-default semantics.
def set_well(block_id,row,column,sample_id,user,expected_revision,dna_ul=None,water_ul=None,note=""):
    return set_position(block_id,row,column,user,expected_revision,kind="sample",sample_id=sample_id,
        dna_ul=UNSET if dna_ul is None else dna_ul,water_ul=UNSET if water_ul is None else water_ul,note=note)

@transaction.atomic
def clear_well(block_id,row,column,user,expected_revision):
    authorise(user);position(row,column);block=locked_block(block_id,expected_revision)
    well=SourceBlockWell.objects.select_related("sample").filter(block=block,row=row,column=column).first()
    if well is None:raise ValidationError("Позиция уже пуста.")
    before=well_snapshot(well);well.delete();advance(block,user)
    record(user,block,{"действие":"Очистка позиции в схеме","было":before,"стало":None,"версия":block.revision})
