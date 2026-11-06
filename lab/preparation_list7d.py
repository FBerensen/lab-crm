from types import MethodType
from django.contrib import admin
from django.db.models import Case,When,Value,CharField,Q,Exists,OuterRef
from django.urls import reverse
from django.utils.html import format_html
from .models import Preparation,Component,PreparationDraftSelection

STAGES=(
    ("draft","Черновик"),
    ("facts","Факт заполняется"),
    ("confirmation","Есть запись подтверждения — актуальность проверить в 7Г"),
    ("posted","Проведено; допуск не зарегистрирован"),
    ("approved","Допуск зарегистрирован"),
    ("review","Несогласованные отметки — нужна проверка"),
    ("other","Другая операция — отдельная проверка"),
)
LABELS=dict(STAGES)

def annotate_list(qs):
    selections=PreparationDraftSelection.objects.filter(preparation_id=OuterRef("pk"))
    return qs.annotate(
        _p7d_actual=Exists(Component.objects.filter(preparation_id=OuterRef("pk"))),
        _p7d_selection=Exists(selections),
        _p7d_confirmation=Exists(selections.filter(facts_confirmed_by__isnull=False,facts_confirmed_at__isnull=False).exclude(facts_digest="")),
    ).annotate(_p7d_stage=Case(
        When(~Q(kind="Приготовление реактива"),then=Value("other")),
        When(Q(processed=False,approved_at__isnull=False)|Q(processed=True,output__isnull=True)|Q(approved_at__isnull=False,approved_by__isnull=True)|Q(approved_at__isnull=True,approved_by__isnull=False),then=Value("review")),
        When(processed=True,approved_at__isnull=False,then=Value("approved")),
        When(processed=True,then=Value("posted")),
        When(_p7d_confirmation=True,then=Value("confirmation")),
        When(Q(actual_volume_ml__isnull=False)|Q(_p7d_actual=True),then=Value("facts")),
        default=Value("draft"),output_field=CharField()))

def stage_code(obj):
    code=getattr(obj,"_p7d_stage",None)
    if code in LABELS:return code
    if obj.kind!="Приготовление реактива":return "other"
    if (not obj.processed and obj.approved_at is not None) or (obj.processed and not obj.output_id) or ((obj.approved_at is None)!=(obj.approved_by_id is None)):return "review"
    if obj.processed:return "approved" if obj.approved_at is not None else "posted"
    if PreparationDraftSelection.objects.filter(preparation_id=obj.pk,facts_confirmed_by__isnull=False,facts_confirmed_at__isnull=False).exclude(facts_digest="").exists():return "confirmation"
    if obj.actual_volume_ml is not None or obj.components.exists():return "facts"
    return "draft"

class PreparationStageFilter(admin.SimpleListFilter):
    title="Этап приготовления";parameter_name="prep_stage"
    def lookups(self,request,model_admin):return STAGES
    def queryset(self,request,queryset):
        value=self.value()
        if value is None:return queryset
        return queryset.filter(_p7d_stage=value) if value in LABELS else queryset.none()

class PreparationOriginFilter(admin.SimpleListFilter):
    title="Источник записи";parameter_name="prep_origin"
    def lookups(self,request,model_admin):return (("7v","Из сохранённого подбора 7В"),("ordinary","Без подбора 7В"))
    def queryset(self,request,queryset):
        value=self.value()
        if value is None:return queryset
        if value not in {"7v","ordinary"}:return queryset.none()
        return queryset.filter(_p7d_selection=(value=="7v"))

class PreparationApprovalFilter(admin.SimpleListFilter):
    title="Отметка допуска";parameter_name="prep_approval"
    def lookups(self,request,model_admin):return (("yes","Дата записана"),("no","Дата отсутствует"))
    def queryset(self,request,queryset):
        value=self.value()
        if value is None:return queryset
        if value not in {"yes","no"}:return queryset.none()
        return queryset.filter(approved_at__isnull=(value=="no"))


def install(site):
    ma=site._registry[Preparation]
    if getattr(ma,"_list7d_installed",False):return
    if not getattr(ma,"_readiness7g_installed",False):raise RuntimeError("7G required")
    if not hasattr(ma,"preparation_title"):raise RuntimeError("Expected existing preparation_title display")
    previous_qs=ma.get_queryset
    old_related=ma.list_select_related
    related=("recipe","recipe__product","output","actor","output_storage_place","approved_by","autoclaved_by")
    if old_related is not True:
        related=tuple(dict.fromkeys((*(old_related or ()),*related)))
    def get_queryset(self,request):
        qs=previous_qs(request)
        qs=qs.select_related() if old_related is True else qs.select_related(*related)
        return annotate_list(qs)
    def workflow_stage(self,obj):return LABELS[stage_code(obj)]
    workflow_stage.short_description="Этап (не разрешение на проведение)"
    def warehouse_state(self,obj):
        if not obj.output_id:return "Готовая партия не создана"
        lot=obj.output
        validity="срок истёк" if lot.expired else ("срок не указан" if lot.expiry is None else "срок не истёк")
        return f"Допуск на складе: {'да' if lot.released else 'нет'}; {validity}; свободно: {lot.free} {lot.unit}"
    warehouse_state.short_description="Текущий складской статус результата"
    def readiness_link(self,obj):
        return format_html('<a href="{}">Предпроверка 7Г</a>',reverse("admin:lab_preparation_readiness",args=[obj.pk]))
    readiness_link.short_description="Проверка"
    ma.get_queryset=MethodType(get_queryset,ma)
    ma.workflow_stage_7d=MethodType(workflow_stage,ma)
    ma.warehouse_state_7d=MethodType(warehouse_state,ma)
    ma.list_readiness_7d=MethodType(readiness_link,ma)
    ma.list_display=("preparation_title","actual_day","actor","recipe","planned_volume_ml","actual_volume_ml",
        "workflow_stage_7d","output","warehouse_state_7d","approved_by","list_readiness_7d")
    ma.list_display_links=("preparation_title",)
    old_filters=tuple(ma.list_filter)
    ma.list_filter=(PreparationStageFilter,PreparationOriginFilter,PreparationApprovalFilter,*old_filters,("recipe",admin.RelatedOnlyFieldListFilter))
    ma.list_select_related=True if old_related is True else related
    ma.ordering=("-actual_day","-pk")
    ma.date_hierarchy="actual_day"
    ma._list7d_installed=True
