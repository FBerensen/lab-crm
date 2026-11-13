from django.core.exceptions import ValidationError
from django.db import transaction
from lab.models import SourceBlock,StoragePlace
from lab.storage_support import place_path
from lab import source_blocks8v as legacy


def location_path(block,places=None):
    if not block.storage_place_id:return "Место не указано"
    if places is None:places=list(StoragePlace.objects.all())
    return place_path(block.storage_place_id,places)


def move_block(block_id,storage_place_id,user,expected_revision):
    legacy.authorise(user)
    block=SourceBlock.objects.get(pk=block_id)
    block.storage_place_id=storage_place_id
    return legacy.save_block(block,user,expected_revision)


def install_services():
    if getattr(legacy,"_storage8d_services",False):return
    old_save=legacy.save_block;old_snapshot=legacy.block_snapshot
    def snapshot(block):
        value=old_snapshot(block)
        value.update({"место_хранения_id":block.storage_place_id,"путь_хранения":location_path(block)})
        return value
    @transaction.atomic
    def guarded_save(block,user,expected_revision):
        legacy.authorise(user)
        # StoragePlace.save locks the same tree rows in the same order.
        places=list(StoragePlace.objects.select_for_update().order_by("pk"))
        mapping={p.pk:p for p in places}
        target=block.storage_place_id
        if target is not None and (isinstance(target,bool) or not isinstance(target,int) or target<=0):
            raise ValidationError("Некорректное место хранения.")
        if block._state.adding:
            previous=None
        else:
            current=SourceBlock.objects.select_for_update().get(pk=block.pk)
            legacy.check_revision(current,expected_revision)
            previous=current.storage_place_id
        if target is not None:
            place=mapping.get(target)
            if place is None:raise ValidationError("Место хранения больше не существует.")
            if target!=previous and not place.active:raise ValidationError("Для нового размещения выберите доступное место хранения.")
        return old_save(block,user,expected_revision)
    legacy.block_snapshot=snapshot;legacy.save_block=guarded_save
    # SourceBlockAdmin imported save_block by value before this hook.
    from lab import source_blocks8v_admin
    source_blocks8v_admin.save_block=guarded_save
    legacy._storage8d_services=True
