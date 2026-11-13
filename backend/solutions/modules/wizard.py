import hashlib
import json
from decimal import Decimal
from types import MethodType
from uuid import uuid4
from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from lab.models import Preparation, PreparationProtocol, ProductCategory, StoragePlace, Component, Audit, convert_preparation_quantity
from lab.presentation import compact_number

PRESETS = (50, 100, 200, 250, 500, 800, 1000)
SALT = "labcrm-preparation-wizard-v1"
AUDIT_ENTITY = "lab.PreparationWizard"


def protocol_fingerprint(protocol):
    product = protocol.product
    data = {"id": protocol.pk, "version": protocol.version, "base": str(protocol.base_volume_ml),
        "active": protocol.active, "instructions": protocol.instructions, "ph": protocol.target_ph,
        "processing": protocol.processing_notes, "product": [product.pk, product.name, product.concentration,
            product.active, product.requires_autoclaving, product.shelf_life_days, product.storage_conditions],
        "components": []}
    for c in protocol.recipe_components.order_by("pk"):
        data["components"].append([c.pk, c.reagent_name, str(c.quantity), c.unit, c.to_final_volume,
            c.source_concentration, c.final_concentration, c.note, c.position,
            list(c.allowed_lots.order_by("pk").values_list("pk", flat=True))])
    return hashlib.sha256(json.dumps(data, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def new_token(user, protocol, volume):
    return signing.dumps({"user": user.pk, "nonce": uuid4().hex, "protocol": protocol.pk,
        "volume": str(volume), "fingerprint": protocol_fingerprint(protocol)}, salt=SALT)


def read_token(token, user):
    try:
        data = signing.loads(token, salt=SALT, max_age=14400)
        if data["user"] != user.pk or len(data["nonce"]) != 32:
            raise ValueError
        return data
    except (signing.BadSignature, KeyError, TypeError, ValueError):
        raise ValidationError("Расчёт устарел или не принадлежит вашей учётной записи. Нажмите «Рассчитать состав».")


class WizardForm(forms.Form):
    recipe = forms.ModelChoiceField(label="Протокол", queryset=PreparationProtocol.objects.none())
    planned_volume_ml = forms.DecimalField(label="Плановый конечный объём, мл", min_value=Decimal("0.0001"), max_digits=16, decimal_places=4)
    actual_day = forms.DateField(label="Дата приготовления", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    actual_volume_ml = forms.DecimalField(label="Фактический выход, мл", min_value=Decimal("0.0001"), max_digits=16, decimal_places=4, required=False)
    output_category = forms.ModelChoiceField(label="Категория готовой партии", queryset=ProductCategory.objects.none(), required=False)
    output_storage_place = forms.ModelChoiceField(label="Место хранения готовой партии", queryset=StoragePlace.objects.none(), required=False)
    output_expiry = forms.DateField(label="Годен до", required=False, widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"))
    note = forms.CharField(label="Замечания", required=False, widget=forms.Textarea(attrs={"rows": 3}))
    request_token = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, protocol=None, final=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.protocol = protocol
        self.final = final
        self.components = list(protocol.recipe_components.order_by("position", "pk")) if protocol else []
        self.fields["recipe"].queryset = PreparationProtocol.objects.filter(active=True, product__active=True).select_related("product")
        self.fields["output_category"].queryset = ProductCategory.objects.filter(active=True)
        self.fields["output_storage_place"].queryset = StoragePlace.objects.filter(active=True)
        for name in ("actual_volume_ml", "output_category", "output_storage_place", "request_token"):
            self.fields[name].required = final
        for c in self.components:
            lots = c.available_lots().select_related("storage_place")
            field = forms.ModelChoiceField(label="Складская партия", queryset=lots, required=final)
            field.label_from_instance = lambda lot: f"{lot.name} | {lot.code} | свободно {compact_number(lot.free)} {lot.unit} | годен до {lot.expiry or 'не указан'}"
            if not self.is_bound and lots.count() == 1:
                field.initial = lots.first().pk
            self.fields[f"lot_{c.pk}"] = field
            self.fields[f"qty_{c.pk}"] = forms.DecimalField(label="Фактический расход", min_value=Decimal("0.0001"), max_digits=16, decimal_places=4, required=final,
                help_text="В единице выбранной складской партии; план сюда не подставляется.")

    def clean_actual_day(self):
        value = self.cleaned_data["actual_day"]
        if value > timezone.localdate():
            raise ValidationError("Дата выполненного приготовления не может быть в будущем.")
        return value

    def clean(self):
        data = super().clean()
        expiry = data.get("output_expiry")
        if expiry and (expiry < timezone.localdate() or expiry < data.get("actual_day", timezone.localdate())):
            self.add_error("output_expiry", "Срок годности уже истёк либо раньше даты приготовления.")
        if self.final and not self.components:
            raise ValidationError("В протоколе нет компонентов. Сначала заполните протокол.")
        if self.final:
            for c in self.components:
                lot = data.get(f"lot_{c.pk}")
                qty = data.get(f"qty_{c.pk}")
                if lot and qty:
                    try:
                        convert_preparation_quantity(qty, lot.unit, c.unit)
                    except ValidationError as exc:
                        self.add_error(f"qty_{c.pk}", exc)
        return data


def preview_rows(form):
    rows = []
    volume = form.cleaned_data.get("planned_volume_ml")
    for c in form.components:
        plan = "Объём не задан"
        lot_plan = "Выберите партию и пересчитайте"
        warning = ""
        if volume:
            try:
                required = c.required_quantity(volume)
                plan = ("Довести до " + compact_number(volume) + " мл") if required is None else compact_number(required) + " " + c.get_unit_display()
                lot = form.cleaned_data.get(f"lot_{c.pk}")
                if lot:
                    converted = c.required_quantity(volume, target_unit=lot.unit)
                    lot_plan = "Расход растворителя укажите по факту" if converted is None else compact_number(converted) + " " + lot.unit
                    if converted is not None and converted > lot.free:
                        warning = "План превышает свободный остаток выбранной партии"
            except ValidationError as exc:
                warning = " ".join(exc.messages)
        rows.append({"component": c, "plan": plan, "lot_plan": lot_plan, "warning": warning,
            "lot": form[f"lot_{c.pk}"], "qty": form[f"qty_{c.pk}"]})
    return rows


def create_draft(user, form):
    from lab.preparation_access import can_manage_preparation_stock
    if not can_manage_preparation_stock(user):
        raise PermissionDenied
    data = form.cleaned_data
    token = read_token(data["request_token"], user)
    with transaction.atomic():
        get_user_model().objects.select_for_update().get(pk=user.pk)
        prior = Audit.objects.filter(actor=user, entity=AUDIT_ENTITY, object_id=token["nonce"]).first()
        if prior:
            return Preparation.objects.get(pk=int(prior.detail.split(":", 1)[1])), False
        protocol = PreparationProtocol.objects.select_for_update().select_related("product").get(pk=data["recipe"].pk)
        if not protocol.active or not protocol.product.active:
            raise ValidationError("Протокол или продукт больше не активен.")
        if token["protocol"] != protocol.pk or Decimal(token["volume"]) != data["planned_volume_ml"] or token["fingerprint"] != protocol_fingerprint(protocol):
            raise ValidationError("Протокол или объём изменился. Нажмите «Рассчитать состав» перед сохранением.")
        p = Preparation(actor=user, actual_day=data["actual_day"], kind="Приготовление реактива",
            recipe=protocol, protocol=str(protocol)[:240], qty=data["actual_volume_ml"],
            planned_volume_ml=data["planned_volume_ml"], actual_volume_ml=data["actual_volume_ml"],
            output_category=data["output_category"], output_storage_place=data["output_storage_place"],
            output_expiry=data.get("output_expiry"), note=data.get("note", ""))
        p.full_clean()
        p.save()
        for c in form.components:
            component = Component(preparation=p, recipe_component=c, lot=data[f"lot_{c.pk}"], qty=data[f"qty_{c.pk}"])
            component.full_clean()
            component.save()
        Audit.objects.create(actor=user, entity=AUDIT_ENTITY, object_id=token["nonce"], detail=f"draft:{p.pk}")
        Audit.objects.create(actor=user, entity=p._meta.label, object_id=str(p.pk), detail="Создано через мастер приготовления. Складские операции не выполнялись.")
    return p, True


def configure_admin(site):
    if getattr(site, "_lab_preparation_stage3", False):
        return
    modeladmin = site._registry[Preparation]
    old_urls = modeladmin.get_urls

    def wizard(self, request):
        if not self.has_add_permission(request):
            raise PermissionDenied
        data = request.POST.copy() if request.method == "POST" else None
        final = bool(data and data.get("action") == "save")
        if data and data.get("preset") in {str(v) for v in PRESETS}:
            data["planned_volume_ml"] = data["preset"]
            final = False
        raw_id = data.get("recipe") if data is not None else request.GET.get("recipe")
        protocol = None
        if str(raw_id or "").isdigit():
            protocol = PreparationProtocol.objects.filter(pk=int(raw_id), active=True, product__active=True).select_related("product").first()
        form = WizardForm(data, protocol=protocol, final=final, initial={"recipe": protocol.pk if protocol else None})
        if data is not None:
            valid = form.is_valid()
            if final and valid:
                try:
                    p, created = create_draft(request.user, form)
                except ValidationError as exc:
                    form.add_error(None, " ".join(exc.messages))
                else:
                    self.message_user(request, "Приготовление сохранено без списания. Проведение выполняется отдельным действием." if created else "Этот запрос уже сохранён; открыт прежний черновик.", messages.SUCCESS)
                    return HttpResponseRedirect(reverse("admin:lab_preparation_change", args=[p.pk]))
            if not final and form.cleaned_data.get("recipe") and form.cleaned_data.get("planned_volume_ml"):
                token = new_token(request.user, form.cleaned_data["recipe"], form.cleaned_data["planned_volume_ml"])
                form.data = form.data.copy()
                form.data["request_token"] = token
        else:
            form.cleaned_data = {}
        return TemplateResponse(request, "admin/lab/preparation/wizard.html", {
            **self.admin_site.each_context(request), "title": "Приготовление по протоколу",
            "opts": self.model._meta, "form": form, "protocol": protocol, "rows": preview_rows(form),
            "presets": PRESETS, "cancel_url": reverse("admin:lab_preparation_changelist"),
        })

    def urls(self):
        return [path("wizard/", self.admin_site.admin_view(self.preparation_wizard), name="lab_preparation_wizard")] + old_urls()

    modeladmin.preparation_wizard = MethodType(wizard, modeladmin)
    modeladmin.get_urls = MethodType(urls, modeladmin)
    modeladmin.change_list_template = "admin/lab/preparation/wizard_change_list.html"
    site._lab_preparation_stage3 = True
