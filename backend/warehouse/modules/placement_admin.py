from types import MethodType
from urllib.parse import urlencode

from django import forms
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import Http404, HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse

from lab.models import StoragePlace
from lab.storage8z import MODELS, place_material
from lab.storage_support import place_path


SALT = "lab.storage8z.confirm.v1"
GROUPS = (
    ("samples", "Образцы и пробы"),
    ("lots", "Складские продукты"),
    ("blocks", "Исходные блоки"),
)


class ConfirmForm(forms.Form):
    token = forms.CharField(widget=forms.HiddenInput)
    confirm = forms.BooleanField(
        label="Подтверждаю размещение объекта целиком в указанном месте"
    )


def allowed_groups(site, request):
    return [
        (key, label)
        for key, label in GROUPS
        if (
            site._registry[MODELS[key]].has_view_permission(request)
            and site._registry[MODELS[key]].has_change_permission(request)
        )
    ]


def can_use_object(modeladmin, request, obj):
    return (
        modeladmin.has_view_permission(request, obj)
        and modeladmin.has_change_permission(request, obj)
    )


def install(site):
    if getattr(site, "_storage8z_installed", False):
        return
    if not getattr(site, "_storage8zh_installed", False):
        raise RuntimeError("8Z requires installed 8ZH")

    place_admin = site._registry[StoragePlace]
    old_urls = place_admin.get_urls
    old_contents = place_admin.contents_view

    def placement_url(pk):
        return reverse(site.name + ":lab_storageplace_place8z", args=[pk])

    def contents(self, request, object_id):
        response = old_contents(request, object_id)
        if not hasattr(response, "context_data"):
            return response
        if response.is_rendered:
            raise RuntimeError("8Z expected unrendered TemplateResponse")
        ctx = response.context_data
        place = ctx["place"]
        ctx["storage8z_place_url"] = (
            placement_url(place.pk)
            if place.active and allowed_groups(site, request)
            else None
        )
        return response

    def placement(self, request, object_id):
        if request.method not in {"GET", "HEAD", "POST"}:
            return HttpResponseNotAllowed(["GET", "HEAD", "POST"])

        place = self.get_object(request, str(object_id))
        if place is None:
            raise Http404
        if not self.has_view_permission(request, place):
            raise PermissionDenied

        groups = allowed_groups(site, request)
        if not groups:
            raise PermissionDenied

        base_url = placement_url(place.pk)
        back_url = reverse(
            site.name + ":lab_storageplace_contents", args=[place.pk]
        )
        places = list(StoragePlace.objects.all())
        ctx = {
            **site.each_context(request),
            "opts": StoragePlace._meta,
            "title": "Разместить материал",
            "place": place,
            "destination_path": place_path(place.pk, places),
            "back_url": back_url,
            "base_url": base_url,
            "groups": groups,
            "rows": [],
        }

        if not place.active:
            ctx["error"] = "Место недоступно для нового размещения."
            return TemplateResponse(
                request, "admin/lab/storageplace/place8z.html", ctx,
                status=400,
            )

        if request.method == "POST":
            form = ConfirmForm(request.POST)
            ctx["form"] = form
            if form.is_valid():
                try:
                    payload = signing.loads(
                        form.cleaned_data["token"],
                        salt=SALT,
                        max_age=900,
                    )
                    if (
                        not isinstance(payload, dict)
                        or payload.get("user") != request.user.pk
                        or payload.get("destination") != place.pk
                    ):
                        raise ValidationError(
                            "Подтверждение относится к другому пользователю "
                            "или месту. Повторите выбор объекта."
                        )

                    obj, changed = place_material(
                        site, request,
                        payload.get("group"),
                        payload.get("object"),
                        place.pk,
                        expected_place_id=payload.get("previous"),
                        expected_revision=payload.get("revision"),
                    )
                    self.message_user(
                        request,
                        "Материал размещён."
                        if changed else "Объект уже находится в этом месте.",
                    )
                    return HttpResponseRedirect(back_url)
                except signing.BadSignature:
                    form.add_error(
                        None,
                        "Подтверждение недействительно или устарело. "
                        "Повторите выбор объекта.",
                    )
                except ValidationError as exc:
                    form.add_error(None, " ".join(exc.messages))

            return TemplateResponse(
                request, "admin/lab/storageplace/place8z.html", ctx
            )

        group = request.GET.get("group", "")
        if not group:
            return TemplateResponse(
                request, "admin/lab/storageplace/place8z.html", ctx
            )
        if group not in dict(groups):
            raise PermissionDenied

        ctx["group"] = group
        modeladmin = site._registry[MODELS[group]]
        qs = modeladmin.get_queryset(request)
        object_id_value = request.GET.get("object")

        if object_id_value is not None:
            try:
                pk = int(object_id_value)
                if pk <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise Http404

            obj = qs.filter(pk=pk).first()
            if obj is None:
                raise Http404
            if not can_use_object(modeladmin, request, obj):
                raise PermissionDenied

            if obj.storage_place_id is not None:
                source = self.get_object(request, str(obj.storage_place_id))
                if source is None or not self.has_view_permission(request, source):
                    raise PermissionDenied

            payload = {
                "user": request.user.pk,
                "destination": place.pk,
                "group": group,
                "object": obj.pk,
                "previous": obj.storage_place_id,
                "revision": obj.revision if group == "blocks" else None,
            }
            ctx.update({
                "preview": True,
                "object_label": (
                    obj.code if group == "samples"
                    else obj.name + " [" + str(obj.code) + "]"
                ),
                "source_path": (
                    place_path(obj.storage_place_id, places)
                    if obj.storage_place_id else "Место не указано"
                ),
                "same_place": obj.storage_place_id == place.pk,
                "is_transfer": (
                    obj.storage_place_id is not None
                    and obj.storage_place_id != place.pk
                ),
                "is_lot": group == "lots",
                "is_block": group == "blocks",
                "form": ConfirmForm(initial={
                    "token": signing.dumps(payload, salt=SALT),
                }),
            })
            return TemplateResponse(
                request, "admin/lab/storageplace/place8z.html", ctx
            )

        query = request.GET.get("q", "").strip()[:200]
        ctx["q"] = query

        if group == "samples":
            qs = qs.select_related("storage_place").order_by(
                "series", "number", "pk"
            )
            if query:
                criterion = (
                    Q(series__icontains=query)
                    | Q(number__icontains=query)
                )
                if " №" in query:
                    series, number = query.rsplit(" №", 1)
                    criterion |= Q(series__iexact=series, number__iexact=number)
                qs = qs.filter(criterion)
        elif group == "lots":
            qs = qs.select_related("storage_place").order_by("name", "pk")
            if query:
                qs = qs.filter(
                    Q(name__icontains=query)
                    | Q(code__icontains=query)
                    | Q(catalog_number__icontains=query)
                )
        else:
            qs = qs.select_related("storage_place").order_by("name", "pk")
            if query:
                criterion = Q(name__icontains=query)
                from lab.storage8d_support import block_code_id
                code_pk = block_code_id(query)
                if code_pk is not None:
                    criterion |= Q(pk=code_pk)
                qs = qs.filter(criterion)

        page = Paginator(qs, 25).get_page(request.GET.get("page"))
        rows = []
        for obj in page.object_list:
            if not can_use_object(modeladmin, request, obj):
                continue
            if obj.storage_place_id is not None:
                source = self.get_object(request, str(obj.storage_place_id))
                if source is None or not self.has_view_permission(request, source):
                    continue
            rows.append({
                "label": (
                    obj.code if group == "samples"
                    else obj.name + " [" + str(obj.code) + "]"
                ),
                "source": (
                    place_path(obj.storage_place_id, places)
                    if obj.storage_place_id else "Место не указано"
                ),
                "url": base_url + "?" + urlencode({
                    "group": group, "object": obj.pk,
                }),
            })

        links = {}
        for label, number in (
            ("previous", page.previous_page_number() if page.has_previous() else None),
            ("next", page.next_page_number() if page.has_next() else None),
        ):
            if number is not None:
                links[label] = base_url + "?" + urlencode({
                    "group": group, "q": query, "page": number,
                })
        ctx.update({"rows": rows, "page": page, "links": links})
        return TemplateResponse(
            request, "admin/lab/storageplace/place8z.html", ctx
        )

    def urls(self):
        return [
            path(
                "<int:object_id>/place-material/",
                site.admin_view(self.placement8z_view),
                name="lab_storageplace_place8z",
            ),
        ] + old_urls()

    place_admin.contents_view = MethodType(contents, place_admin)
    place_admin.placement8z_view = MethodType(placement, place_admin)
    place_admin.get_urls = MethodType(urls, place_admin)
    site._storage8z_installed = True