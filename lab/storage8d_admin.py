from types import MethodType
from urllib.parse import urlencode
from django.contrib import admin
from django.core.paginator import Paginator
from django.db.models import Count,Q,Value,IntegerField,Prefetch,OuterRef,Subquery
from django.db.models.functions import Coalesce
from django.urls import reverse
from .models import StoragePlace,SourceBlock,SourceBlockWell
from .storage_support import descendant_ids,place_path
from .storage8d_support import occupancy,block_code_id
from .storage8d import install_services,location_path


def install(site):
    if getattr(site,"_storage8d_installed",False):return
    if not getattr(site,"_lab_storage_stage4",False) or not getattr(site,"_source_blocks8g_installed",False):
        raise RuntimeError("8Д requires existing storage stage4 and accepted stage8G")
    install_services()
    places_admin=site._registry[StoragePlace];blocks_admin=site._registry[SourceBlock]
    old_place_queryset=places_admin.get_queryset;old_contents=places_admin.contents_view
    def place_queryset(self,request):
        qs=old_place_queryset(request)
        if blocks_admin.has_view_permission(request):
            totals=SourceBlock.objects.filter(storage_place_id=OuterRef("pk")).order_by().values("storage_place_id").annotate(total=Count("pk")).values("total")[:1]
            count=Coalesce(Subquery(totals,output_field=IntegerField()),Value(0),output_field=IntegerField())
        else:count=Value(None,output_field=IntegerField())
        return qs.annotate(ui_block_count8d=count)
    @admin.display(description="Исходных блоков непосредственно здесь",ordering="ui_block_count8d")
    def blocks_count(self,obj):return obj.ui_block_count8d if obj.ui_block_count8d is not None else "—"
    def contents(self,request,object_id):
        response=old_contents(request,object_id)
        if response.is_rendered:raise RuntimeError("Expected unrendered storage TemplateResponse")
        context=response.context_data;place=context["place"]
        places=list(StoragePlace.objects.all())
        ids=descendant_ids(place.pk,places) if context["include_children"] else {place.pk}
        can_blocks=blocks_admin.has_view_permission(request)
        block_rows=[];page=None;links={};unplaced=0
        if can_blocks:
            base=blocks_admin.get_queryset(request)
            unplaced=base.filter(storage_place__isnull=True).count()
            qs=base.filter(storage_place_id__in=ids).select_related("storage_place").order_by("name","pk")
            query=context["q"]
            if query:
                criterion=Q(name__icontains=query)|Q(note__icontains=query)|Q(wells__raw_label__icontains=query)|Q(wells__sample__series__icontains=query)|Q(wells__sample__number__icontains=query)
                if " №" in query:
                    series,number=query.rsplit(" №",1)
                    criterion|=Q(wells__sample__series__iexact=series,wells__sample__number__iexact=number)
                code=block_code_id(query)
                if code is not None:criterion|=Q(pk=code)
                qs=qs.filter(criterion).distinct()
            qs=qs.prefetch_related(Prefetch("wells",queryset=SourceBlockWell.objects.only("id","block_id","kind","sample_id"),to_attr="_storage8d_wells"))
            page=Paginator(qs,50).get_page(request.GET.get("block_page"))
            for block in page.object_list:
                block_rows.append({"obj":block,"url":reverse(site.name+":lab_sourceblock_change",args=[block.pk]),
                    "layout_url":reverse(site.name+":lab_sourceblock_layout",args=[block.pk]),"path":place_path(block.storage_place_id,places),"stats":occupancy(block._storage8d_wells)})
            for label,number in (("previous",page.previous_page_number() if page.has_previous() else None),("next",page.next_page_number() if page.has_next() else None)):
                if number is not None:
                    params=request.GET.copy();params["block_page"]=number;links[label]="?"+params.urlencode()
        context.update({"can_blocks":can_blocks,"block_rows":block_rows,"block_page":page,"block_links":links,"unplaced_blocks":unplaced,
            "unplaced_blocks_url":reverse(site.name+":lab_sourceblock_changelist")+"?"+urlencode({"storage_place__isnull":"1"})})
        response.template_name="admin/lab/storageplace/contents8d.html"
        return response
    places_admin.get_queryset=MethodType(place_queryset,places_admin)
    places_admin.contents_view=MethodType(contents,places_admin)
    places_admin.ui_blocks_count8d=MethodType(blocks_count,places_admin)
    fields=list(places_admin.list_display)
    fields.insert(fields.index("contents_link") if "contents_link" in fields else len(fields),"ui_blocks_count8d")
    places_admin.list_display=tuple(fields)

    old_form=blocks_admin.get_form;old_render=blocks_admin.render
    def block_form(self,request,obj=None,**kwargs):
        base=old_form(request,obj,**kwargs)
        class LocatedForm(base):
            def __init__(self,*args,**form_kwargs):
                super().__init__(*args,**form_kwargs)
                if "storage_place" not in self.fields:return
                current=self.instance.storage_place_id if self.instance.pk else None
                scope=places_admin.get_queryset(request) if places_admin.has_view_permission(request) else StoragePlace.objects.none()
                # Same availability rule as existing sample storage forms: selected inactive node is retained.
                self.fields["storage_place"].queryset=scope.filter(Q(active=True)|Q(pk=current))
                self.fields["storage_place"].required=False
                self.fields["storage_place"].empty_label="Место не указано"
                all_places=list(scope)
                self.fields["storage_place"].label_from_instance=lambda place:place_path(place.pk,all_places)+(" [недоступно для нового размещения]" if not place.active else "")
                self.fields["storage_place"].help_text="Размещается весь блок. Места исходных проб и складские остатки не меняются."
        return LocatedForm
    @admin.display(description="Полный путь хранения")
    def storage_path(self,obj):return location_path(obj) if obj and obj.pk else "Место не указано"
    def render(self,request,template,context):
        context=dict(context);plate=context.get("plate")
        if isinstance(plate,SourceBlock):
            places=list(StoragePlace.objects.all())
            destination=next((p for p in places if p.pk==plate.storage_place_id),None)
            can_view=destination is None or places_admin.has_view_permission(request,destination)
            context["storage_path8d"]=location_path(plate,places) if can_view else "Доступ к месту ограничен"
            context["storage_url8d"]=reverse(site.name+":lab_storageplace_contents",args=[destination.pk]) if destination and can_view else None
        return old_render(request,template,context)
    blocks_admin.get_form=MethodType(block_form,blocks_admin)
    blocks_admin.render=MethodType(render,blocks_admin)
    blocks_admin.storage_path8d=MethodType(storage_path,blocks_admin)
    fields=list(blocks_admin.fields);fields.insert(fields.index("note"),"storage_place");fields.insert(fields.index("layout_link"),"storage_path8d")
    blocks_admin.fields=tuple(fields)
    blocks_admin.readonly_fields=tuple(dict.fromkeys((*blocks_admin.readonly_fields,"storage_path8d")))
    fields=list(blocks_admin.list_display);fields.insert(fields.index("filled_count"),"storage_place");blocks_admin.list_display=tuple(fields)
    blocks_admin.list_filter=tuple(dict.fromkeys((*blocks_admin.list_filter,"storage_place")))
    blocks_admin.list_select_related=tuple(dict.fromkeys((*blocks_admin.list_select_related,"storage_place")))
    blocks_admin.search_fields=tuple(dict.fromkeys((*blocks_admin.search_fields,"storage_place__name")))
    from . import source_blocks8v_admin as module
    marker='<p>Заполнено:'
    if not getattr(module,"_storage8d_layout",False):
        if module.LAYOUT.count(marker)!=1:raise RuntimeError("Unexpected 8G layout anchor")
        module.LAYOUT=module.LAYOUT.replace(marker,'<p>Расположение: {{ storage_path8d }}. {% if storage_url8d %}<a href="{{ storage_url8d }}">Содержимое места</a>{% endif %}</p>'+marker,1)
        module._storage8d_layout=True
    site._storage8d_installed=True
