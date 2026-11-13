from decimal import Decimal
from django import forms
from django.contrib import admin,messages
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Count
from django.http import Http404,HttpResponse,HttpResponseRedirect,HttpResponseNotAllowed
from django.template import engines
from django.urls import path,reverse
from django.utils.html import format_html
from lab.models import SourceBlock,SourceBlockWell,Sample
from lab.source_blocks8v_models import ROWS
from lab.source_blocks8v import save_block,set_position,clear_well,position
from lab.sample_batch import can_edit_samples
from lab.source_blocks8g_admin import ExcelMixin

def number(value):
    if value is None:return "не указано"
    text=format(value,"f");return text.rstrip("0").rstrip(".") if "." in text else text

class BlockForm(forms.ModelForm):
    expected_revision=forms.IntegerField(widget=forms.HiddenInput,min_value=0,label="")
    class Meta:
        model=SourceBlock
        fields=("name","default_dna_ul","default_water_ul","note")
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.initial["expected_revision"]=self.instance.revision if self.instance.pk else 0
        for key in ("default_dna_ul","default_water_ul"):
            self.fields[key].help_text="На новую лунку с пробой или незарегистрированной подписью. Ранее заполненные лунки не меняются. Для водного контроля воду задают отдельно."
    def clean(self):
        data=super().clean()
        if self.instance.pk and "expected_revision" in data:
            if SourceBlock.objects.filter(pk=self.instance.pk).values_list("revision",flat=True).first()!=data["expected_revision"]:
                raise ValidationError("Блок изменился. Обновите страницу перед сохранением.")
        return data

class WellForm(forms.Form):
    expected_revision=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    kind=forms.ChoiceField(label="Тип позиции",choices=SourceBlockWell.Kind.choices,required=False,initial="sample")
    sample=forms.ModelChoiceField(label="Серия и номер пробы",queryset=Sample.objects.none(),required=False)
    raw_label=forms.CharField(label="Незарегистрированное обозначение",max_length=500,required=False,strip=False)
    dna_ul=forms.DecimalField(label="ДНК, мкл",max_digits=16,decimal_places=4,min_value=Decimal("0"),localize=True)
    water_ul=forms.DecimalField(label="Вода, мкл",max_digits=16,decimal_places=4,min_value=Decimal("0"),localize=True,required=False,
        help_text="У контроля можно оставить неизвестным. Пустое значение не равно нулю.")
    note=forms.CharField(label="Примечание к лунке",required=False,widget=forms.Textarea(attrs={"rows":3}))
    def clean(self):
        data=super().clean();kind=data.get("kind") or "sample";data["kind"]=kind
        if kind=="sample" and not data.get("sample"):self.add_error("sample","Выберите зарегистрированную пробу.")
        if kind!="sample" and data.get("sample"):self.add_error("sample","Этот тип не связан с карточкой пробы.")
        if kind=="unregistered" and not data.get("raw_label","").strip():self.add_error("raw_label","Введите подпись.")
        if kind!="unregistered" and data.get("raw_label"):self.add_error("raw_label","Свободная подпись допустима только для незарегистрированной позиции.")
        if kind=="water" and data.get("dna_ul") is not None and data["dna_ul"]!=0:self.add_error("dna_ul","У водного контроля ДНК = 0.")
        if kind!="water" and data.get("water_ul") is None:self.add_error("water_ul","Укажите объём воды.")
        return data

class ClearForm(forms.Form):
    expected_revision=forms.IntegerField(min_value=0,widget=forms.HiddenInput)
    confirm_clear=forms.BooleanField(label="Подтверждаю очистку позиции. Карточка пробы не удаляется.")

LAYOUT="""{% extends "admin/base_site.html" %}{% block extrastyle %}{{ block.super }}<style>
.sb8v-scroll{overflow-x:auto;margin:16px 0}.sb8v-plate{border-collapse:collapse;min-width:1100px;width:100%}
.sb8v-plate td,.sb8v-plate th{border:1px solid var(--hairline-color,#aaa);padding:8px;text-align:center;vertical-align:top}
.sb8v-plate td{min-width:76px;height:80px}.sb8v-plate small{display:block;margin-top:5px}
.sb8v-filled{background:var(--selected-bg,#e4f1e4)}.sb8g-water{border-bottom:4px solid #4c88b5!important}.sb8g-raw{border-bottom:4px solid #bf8a16!important}
.sb8v-tools{display:flex;gap:16px;flex-wrap:wrap;margin:14px 0}
</style>{% endblock %}{% block content %}
<div class="sb8v-tools"><a href="{{ card_url }}">Карточка блока</a><a href="{{ list_url }}">Все исходные блоки</a>
<a href="{{ export_url }}">Экспорт Excel</a><a href="{{ template_url }}">Скачать шаблон</a>{% if can_edit %}<a href="{{ import_url }}">Импорт Excel</a>{% endif %}</div>
<p>Заполнено: {{ occupied }} / 96. Лунок с пробами: {{ sample_wells }}; различных зарегистрированных проб: {{ unique_samples }};
водных контролей: {{ controls }}; незарегистрированных позиций: {{ raw_count }}; пустых: {{ empty_count }}. Версия: {{ plate.revision }}.</p>
<p>Общие значения для новых лунок с пробами/подписями: ДНК {{ dna_default }} мкл; вода {{ water_default }} мкл. Ранее заполненные позиции сохраняются.</p>
<p>Сумма указанных объёмов: ДНК {{ dna_total }} мкл; вода {{ water_total }} мкл. Позиций с неизвестным объёмом воды: {{ unknown_water }}.
Это не складской расход и не остаток ДНК.</p>
<div class="sb8v-scroll"><table class="sb8v-plate"><caption>Схема исходного блока {{ plate.code }}</caption><thead><tr><th>Ряд</th>{% for col in columns %}<th>{{ col }}</th>{% endfor %}</tr></thead>
<tbody>{% for row in rows %}<tr><th>{{ row.label }}</th>{% for cell in row.cells %}<td class="{% if cell.well %}sb8v-filled{% endif %} {% if cell.well.kind == 'water' %}sb8g-water{% elif cell.well.kind == 'unregistered' %}sb8g-raw{% endif %}">
{% if can_edit %}<a href="{{ cell.url }}" aria-label="Лунка {{ cell.position }}">{% endif %}{% if cell.well %}{{ cell.well.display_label }}{% else %}{{ cell.position }} · пусто{% endif %}{% if can_edit %}</a>{% endif %}
{% if cell.well %}<small>{{ cell.well.get_kind_display }}<br>ДНК {{ cell.dna }} мкл<br>Вода {{ cell.water }}{% if cell.well.water_ul != None %} мкл{% endif %}</small>{% endif %}</td>{% endfor %}</tr>{% endfor %}</tbody></table></div>
<h2>Табличное представление</h2><div class="sb8v-scroll"><table><thead><tr><th>Позиция</th><th>Обозначение</th><th>Статус</th><th>ДНК, мкл</th><th>Вода, мкл</th><th>Примечание</th></tr></thead><tbody>
{% for cell in filled %}<tr><td>{% if can_edit %}<a href="{{ cell.url }}">{{ cell.position }}</a>{% else %}{{ cell.position }}{% endif %}</td><td>{{ cell.well.display_label }}</td><td>{{ cell.well.get_kind_display }}</td><td>{{ cell.dna }}</td><td>{{ cell.water }}</td><td>{{ cell.well.note }}</td></tr>
{% empty %}<tr><td colspan="6">Блок пока пуст.</td></tr>{% endfor %}</tbody></table></div>
<p>Очистка удаляет только размещение. Регистрация подписи выполняется отдельно: сначала создайте карточку пробы, затем явно выберите её в нужной лунке.</p>{% endblock %}"""

CELL="""{% extends "admin/base_site.html" %}{% block content %}<p><a href="{{ layout_url }}">Вернуться к схеме</a> · {{ plate }}</p>
<p>Позиция {{ address }}. Незарегистрированная подпись не создаёт карточку пробы. Для водного контроля ДНК = 0; неизвестную воду оставьте пустой.</p>
<form method="post" id="source-position-form">{% csrf_token %}{{ form.as_p }}<button type="submit" name="operation" value="save">Сохранить лунку</button></form>
{% if well %}<hr><h2>Очистить позицию</h2><form method="post">{% csrf_token %}{{ clear_form.as_p }}<button type="submit" name="operation" value="clear">Очистить позицию в схеме</button></form>{% endif %}
<script>(function(){var k=document.getElementById('id_kind'),s=document.getElementById('id_sample'),r=document.getElementById('id_raw_label'),d=document.getElementById('id_dna_ul'),w=document.getElementById('id_water_ul');if(!k)return;
function update(change){var type=k.value||'sample';s.disabled=type!=='sample';r.disabled=type!=='unregistered';if(change&&type==='water'){d.value='0';w.value='';}d.readOnly=type==='water';}
k.addEventListener('change',function(){update(true);});update(false);})();</script>{% endblock %}"""

class SourceBlockAdmin(ExcelMixin,admin.ModelAdmin):
    form=BlockForm;actions=None
    list_display=("block_code","name","filled_count","layout_link","default_dna_ul","default_water_ul","updated_at","updated_by")
    search_fields=("name","note","wells__sample__series","wells__sample__number","wells__raw_label")
    readonly_fields=("block_code","layout_link","revision","created_by","updated_by","created_at","updated_at")
    fields=("name","default_dna_ul","default_water_ul","note","expected_revision","block_code","layout_link","revision","created_by","updated_by","created_at","updated_at")
    list_select_related=("created_by","updated_by")
    def has_module_permission(self,request):return self.admin_site._registry[Sample].has_module_permission(request)
    def has_view_permission(self,request,obj=None):return bool(request.user.is_active and request.user.is_staff and self.admin_site._registry[Sample].has_view_permission(request))
    def has_add_permission(self,request):return bool(self.has_view_permission(request) and can_edit_samples(request.user))
    def has_change_permission(self,request,obj=None):return self.has_add_permission(request)
    def has_delete_permission(self,request,obj=None):return False
    @admin.display(description="Код блока")
    def block_code(self,obj):return obj.code
    def get_queryset(self,request):return super().get_queryset(request).annotate(_filled=Count("wells",distinct=True))
    @admin.display(description="Заполнено лунок",ordering="_filled")
    def filled_count(self,obj):return f"{obj._filled} / 96"
    @admin.display(description="Схема и таблица")
    def layout_link(self,obj):
        return format_html('<a href="{}">Открыть схему и таблицу</a>',self.url("layout",obj.pk)) if obj.pk else "Сначала сохраните карточку блока."
    def url(self,action,*args):return reverse(self.admin_site.name+":lab_sourceblock_"+action,args=args)
    def save_model(self,request,obj,form,change):
        if not self.has_change_permission(request,obj):raise PermissionDenied
        save_block(obj,request.user,form.cleaned_data["expected_revision"])
    def changeform_view(self,request,object_id=None,form_url="",extra_context=None):
        try:return super().changeform_view(request,object_id,form_url,extra_context)
        except ValidationError as exc:
            self.message_user(request," ".join(exc.messages),messages.ERROR);return HttpResponseRedirect(request.path)
    def get_urls(self):
        return [path("<int:pk>/layout/",self.admin_site.admin_view(self.layout),name="lab_sourceblock_layout"),
            path("<int:pk>/well/<str:row>/<int:column>/",self.admin_site.admin_view(self.cell),name="lab_sourceblock_cell")]+super().get_urls()
    def get_block(self,request,pk,edit=False):
        if not (self.has_change_permission(request) if edit else self.has_view_permission(request)):raise PermissionDenied
        block=self.get_object(request,str(pk))
        if block is None:raise Http404
        return block
    def render(self,request,template,context):
        request.current_app=self.admin_site.name
        return HttpResponse(engines["django"].from_string(template).render({**self.admin_site.each_context(request),"opts":SourceBlock._meta,**context},request))
    def layout(self,request,pk):
        if request.method not in {"GET","HEAD"}:return HttpResponseNotAllowed(["GET","HEAD"])
        block=self.get_block(request,pk);wells=list(block.wells.select_related("sample"));mapping={(w.row,w.column):w for w in wells};rows=[];filled=[]
        for row in ROWS:
            cells=[]
            for col in range(1,13):
                well=mapping.get((row,col));cell={"position":f"{row}{col}","well":well,"url":self.url("cell",pk,row,col)}
                if well:cell.update(dna=number(well.dna_ul),water=number(well.water_ul));filled.append(cell)
                cells.append(cell)
            rows.append({"label":row,"cells":cells})
        return self.render(request,LAYOUT,{"title":str(block),"plate":block,"rows":rows,"columns":range(1,13),"filled":filled,
            "occupied":len(wells),"empty_count":96-len(wells),"sample_wells":sum(w.kind=="sample" for w in wells),
            "unique_samples":len({w.sample_id for w in wells if w.sample_id}),"controls":sum(w.kind=="water" for w in wells),"raw_count":sum(w.kind=="unregistered" for w in wells),
            "dna_default":number(block.default_dna_ul),"water_default":number(block.default_water_ul),
            "dna_total":number(sum((w.dna_ul for w in wells),Decimal(0))),"water_total":number(sum((w.water_ul for w in wells if w.water_ul is not None),Decimal(0))),
            "unknown_water":sum(w.water_ul is None for w in wells),"can_edit":self.has_change_permission(request,block),
            "card_url":reverse(self.admin_site.name+":lab_sourceblock_change",args=[pk]),"list_url":reverse(self.admin_site.name+":lab_sourceblock_changelist"),
            "export_url":self.url("excel_export",pk),"template_url":self.url("excel_template",pk),"import_url":self.url("excel_import",pk)})
    def cell(self,request,pk,row,column):
        if request.method not in {"GET","HEAD","POST"}:return HttpResponseNotAllowed(["GET","HEAD","POST"])
        block=self.get_block(request,pk,edit=True)
        try:position(row,column)
        except ValidationError:raise Http404
        well=block.wells.filter(row=row,column=column).first()
        initial={"expected_revision":block.revision,"kind":well.kind if well else "sample","raw_label":well.raw_label if well else "",
            "sample":well.sample_id if well else None,"dna_ul":well.dna_ul if well else block.default_dna_ul,
            "water_ul":well.water_ul if well else block.default_water_ul,"note":well.note if well else ""}
        operation=request.POST.get("operation") if request.method=="POST" else None
        form=WellForm(request.POST if operation=="save" else None,initial=initial)
        form.fields["sample"].queryset=self.sample_scope(request).order_by("series","number")
        clear_form=ClearForm(request.POST if operation=="clear" else None,initial={"expected_revision":block.revision})
        if request.method=="POST":
            if operation not in {"save","clear"}:return HttpResponse("Неизвестная операция.",status=400)
            target=form if operation=="save" else clear_form
            if target.is_valid():
                data=target.cleaned_data
                try:
                    if operation=="save":
                        set_position(pk,row,column,request.user,data["expected_revision"],kind=data["kind"],sample_id=data["sample"].pk if data.get("sample") else None,
                            label=data.get("raw_label",""),dna_ul=data["dna_ul"],water_ul=data["water_ul"],note=data["note"])
                    else:clear_well(pk,row,column,request.user,data["expected_revision"])
                    self.message_user(request,"Лунка сохранена." if operation=="save" else "Позиция очищена.",messages.SUCCESS)
                    return HttpResponseRedirect(self.url("layout",pk))
                except ValidationError as exc:target.add_error(None," ".join(exc.messages))
        return self.render(request,CELL,{"title":f"{block.code} / лунка {row}{column}","address":f"{row}{column}","plate":block,"well":well,
            "form":form,"clear_form":clear_form,"layout_url":self.url("layout",pk)})

def install(site):
    if getattr(site,"_source_blocks8v_installed",False):return
    if not getattr(site,"_navigation8b_installed",False):raise RuntimeError("Для 8Г требуется навигация 8Б.")
    if not site.is_registered(SourceBlock):site.register(SourceBlock,SourceBlockAdmin)
    site._source_blocks8v_installed=True;site._source_blocks8g_installed=True
