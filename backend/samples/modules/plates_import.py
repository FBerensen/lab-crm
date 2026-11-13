import hashlib
from datetime import timedelta
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from lab.models import SourceBlock, SourceBlockWell, Sample
from lab.source_blocks8v_models import SourceBlockImport
from lab.source_blocks8v import authorise,check_revision,entry,equal_entries,store_entry,advance
from lab.source_blocks8g_excel import parse,ExcelError
from lab.worklog6 import record

SALT="labcrm-source-block-import-8g"

def caption(data):
    if data is None:return "Пусто"
    return data["sample_code"] if data["kind"]=="sample" else ("вода" if data["kind"]=="water" else data["label"])

def receipt_token(receipt):
    return signing.dumps({"id":str(receipt.pk),"block":receipt.block_id,"user":receipt.created_by_id},salt=SALT)

def token_receipt(token,user,block_id):
    try:
        data=signing.loads(token,salt=SALT,max_age=900)
        if data["user"]!=user.pk or data["block"]!=block_id:raise PermissionDenied
        receipt=SourceBlockImport.objects.get(pk=data["id"],block_id=block_id,created_by=user)
    except (signing.BadSignature,ValueError,KeyError,SourceBlockImport.DoesNotExist):
        raise ValidationError("Предпросмотр недействителен или устарел. Загрузите файл заново.")
    return receipt


def create_preview(data,block_id,user,*,sample_scope,mode="fill",clear=False):
    authorise(user)
    if mode not in {"fill","update"} or clear and mode!="update":raise ValidationError("Некорректный режим импорта.")
    block=SourceBlock.objects.get(pk=block_id)
    parsed_revision=block.revision
    catalog=list(sample_scope.values("id","series","number"))
    try:parsed=parse(data,block.default_dna_ul,block.default_water_ul,catalog)
    except ExcelError as exc:raise ValidationError(str(exc))
    with transaction.atomic():
        block=SourceBlock.objects.select_for_update().get(pk=block_id)
        # Parsing defaults must belong to the same version displayed in the preview.
        if block.revision!=parsed_revision:
            raise ValidationError("Общие значения блока изменились. Повторите загрузку.")
        existing={(w.row,w.column):entry(w) for w in block.wells.select_related("sample")}
        changes=[];unchanged=0;preserved=0
        for incoming in parsed["entries"]:
            key=(incoming["row"],incoming["column"]);before=existing.get(key)
            if incoming.get("empty"):
                after=None if clear else before
            elif mode=="fill" and before is not None:
                after=before
                if not equal_entries(before,incoming):preserved+=1
            else:
                after=dict(incoming)
                if before and not parsed["metadata"]["notes_supplied"]:after["note"]=before["note"]
                if before and before["kind"]=="water" and after["kind"]=="water" and not parsed["metadata"]["water_supplied"]:
                    after["water"]=before["water"]
            if equal_entries(before,after):unchanged+=1;continue
            changes.append({"row":key[0],"column":key[1],"before":before,"after":after})
        payload={"changes":changes,"metadata":parsed["metadata"],"mode":mode,"clear":bool(clear),"unchanged":unchanged,"preserved":preserved,
            "unregistered":sum(1 for c in changes if c["after"] and c["after"]["kind"]=="unregistered"),
            "source_unregistered":sum(1 for e in parsed["entries"] if e.get("kind")=="unregistered")}
        return SourceBlockImport.objects.create(block=block,created_by=user,expires_at=timezone.now()+timedelta(minutes=15),
            expected_revision=block.revision,file_digest=hashlib.sha256(data).hexdigest(),payload=payload)

@transaction.atomic
def apply_preview(receipt_id,block_id,user,*,sample_scope,acknowledge_raw=False,acknowledge_clear=False):
    authorise(user)
    from lab.sample_projects8a import graph_lock
    graph_lock()
    try:receipt=SourceBlockImport.objects.select_for_update().get(pk=receipt_id,block_id=block_id,created_by=user)
    except SourceBlockImport.DoesNotExist:raise PermissionDenied
    if receipt.applied_at is not None:return receipt,False
    if receipt.expires_at<timezone.now():raise ValidationError("Срок предпросмотра истёк. Загрузите файл заново.")
    payload=receipt.payload
    if payload["source_unregistered"] and not acknowledge_raw:raise ValidationError("Подтвердите незарегистрированные обозначения.")
    if payload["clear"] and not acknowledge_clear:raise ValidationError("Подтвердите выбранный режим очистки.")
    block=SourceBlock.objects.select_for_update().get(pk=block_id);check_revision(block,receipt.expected_revision)
    changes=payload["changes"]
    sample_ids={c["after"]["sample_id"] for c in changes if c["after"] and c["after"]["kind"]=="sample"}
    allowed={s.pk:s for s in sample_scope.filter(pk__in=sample_ids)}
    if set(allowed)!=sample_ids:raise ValidationError("Одна из проб больше недоступна. Повторите предпросмотр.")
    for change in changes:
        after=change["after"]
        if after and after["kind"]=="sample" and allowed[after["sample_id"]].code!=after["sample_code"]:
            raise ValidationError("Обозначение одной из проб изменилось. Повторите предпросмотр.")
    # All writes are within this atomic transaction; any error rolls everything back.
    for change in changes:
        after=change["after"]
        if after is None:
            SourceBlockWell.objects.filter(block=block,row=change["row"],column=change["column"]).delete()
        else:store_entry(block,after,user)
    if changes:
        advance(block,user)
        record(user,block,{"действие":"Импорт Excel исходного блока","операция":str(receipt.pk),"sha256":receipt.file_digest,
            "источник":payload["metadata"],"режим":payload["mode"],"очистка":payload["clear"],"изменения":changes,"версия":block.revision})
    receipt.applied_at=timezone.now();receipt.result_revision=block.revision
    receipt.save(update_fields=["applied_at","result_revision"])
    return receipt,True
