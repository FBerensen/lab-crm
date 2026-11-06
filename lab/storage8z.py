import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from .models import Audit, Lot, Sample, SourceBlock, StoragePlace
from .storage8d import move_block


MODELS = {
    "samples": Sample,
    "lots": Lot,
    "blocks": SourceBlock,
}


def positive_id(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValidationError("Некорректный идентификатор объекта или места.")
    return value


def check_access(modeladmin, request, obj):
    if not (
        modeladmin.has_view_permission(request, obj)
        and modeladmin.has_change_permission(request, obj)
    ):
        raise PermissionDenied


@transaction.atomic
def place_material(
    site, request, group, object_id, destination_id,
    *, expected_place_id, expected_revision=None
):
    """Move an existing whole object; never create or split stock."""
    user = request.user
    if not (user.is_active and user.is_staff):
        raise PermissionDenied
    if group not in MODELS:
        raise ValidationError("Неизвестный тип материала.")

    positive_id(object_id)
    positive_id(destination_id)
    if expected_place_id is not None:
        positive_id(expected_place_id)

    model = MODELS[group]
    modeladmin = site._registry[model]
    place_admin = site._registry[StoragePlace]

    # Match the tree-lock order used by StoragePlace.save and block services.
    places = list(StoragePlace.objects.select_for_update().order_by("pk"))
    mapping = {place.pk: place for place in places}
    destination = mapping.get(destination_id)
    if destination is None:
        raise ValidationError("Место хранения больше не существует.")

    if not place_admin.get_queryset(request).filter(pk=destination_id).exists():
        raise PermissionDenied
    if not place_admin.has_view_permission(request, destination):
        raise PermissionDenied
    if not destination.active:
        raise ValidationError("Для размещения выберите активное место хранения.")

    if not modeladmin.get_queryset(request).filter(pk=object_id).exists():
        raise PermissionDenied

    try:
        obj = model.objects.select_for_update().get(pk=object_id)
    except model.DoesNotExist:
        raise ValidationError("Объект больше не существует.")

    check_access(modeladmin, request, obj)

    previous_id = obj.storage_place_id
    if previous_id != expected_place_id:
        raise ValidationError(
            "Место объекта изменилось после открытия формы. "
            "Обновите страницу и повторите выбор."
        )

    if previous_id is not None:
        previous = mapping.get(previous_id)
        if (
            previous is None
            or not place_admin.get_queryset(request).filter(pk=previous_id).exists()
            or not place_admin.has_view_permission(request, previous)
        ):
            raise PermissionDenied

    if group == "blocks":
        from .source_blocks8v import check_revision
        check_revision(obj, expected_revision)

    if previous_id == destination_id:
        return obj, False

    if group == "blocks":
        obj = move_block(obj.pk, destination_id, user, expected_revision)
    else:
        obj.storage_place = destination
        obj.save(update_fields=["storage_place"])
        Audit.objects.create(
            actor=user,
            entity=obj._meta.label,
            object_id=str(obj.pk),
            detail=json.dumps({
                "действие": "Размещение материала со страницы места хранения",
                "было": {
                    "место_id": previous_id,
                    "название": mapping[previous_id].name if previous_id else None,
                },
                "стало": {
                    "место_id": destination.pk,
                    "название": destination.name,
                },
            }, ensure_ascii=False),
        )

    return obj, True