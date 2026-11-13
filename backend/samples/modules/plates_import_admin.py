from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError, PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect, HttpResponseNotAllowed
from django.urls import path
from django.utils import timezone
from lab.models import Sample
from lab.source_blocks8v import entry
from lab.source_blocks8g import create_preview,apply_preview,receipt_token,token_receipt,caption
from lab.source_blocks8g_excel import export,ExcelError,MAX_BYTES

class UploadForm(forms.Form):
    file=forms.FileField(label="Файл XLSX")
    mode=forms.ChoiceField(label="Режим",choices=[("fill","Заполнить только пустые позиции"),("update","Обновить указанные позиции")])
    clear=forms.BooleanField(label="Очистить существующие позиции, пустые в файле",required=False)
    def clean(self):
        data=super().clean()
        if data.get("clear") and data.get("mode")!="update":raise ValidationError("Очистка доступна только в режиме обновления.")
        upload=data.get("file")
        if upload and (not upload.name.lower().endswith(".xlsx") or upload.size>MAX_BYTES):raise ValidationError("Нужен XLSX размером до 2 МБ.")
        return data

UPLOAD="""{% extends "admin/base_site.html" %}{% block content %}
<p><a href="{{ layout_url }}">Вернуться к схеме</a></p>
<p>Получатель: {{ plate }}. Загрузка создаёт техническую запись предпросмотра, но не меняет блок, пробы или склад.</p>
<p>Основная сетка: B8:M15; заголовки: B7:M7 и A8:A15. Водный контроль — «вода». Неизвестные подписи потребуют подтверждения.</p>
<p>Объёмы файла используются для импортируемых позиций. Настройки, название, автор и дата блока не перезаписываются.</p>
<form method="post" enctype="multipart/form-data">{% csrf_token %}{{ form.as_p }}<button type="submit">Проверить и показать изменения</button></form>
{% endblock %}"""

PREVIEW="""{% extends "admin/base_site.html" %}{% block content %}
<p><a href="{{ upload_url }}">Загрузить заново</a> · <a href="{{ layout_url }}">Схема блока</a></p>
<p>Получатель: {{ plate }}. Версия предпросмотра: {{ receipt.expected_revision }}.</p>
<p>Изменяемых позиций: {{ count }}. Без изменения: {{ payload.unchanged }}. Существующих позиций сохранено вместо перезаписи: {{ payload.preserved }}.</p>
<p>Источник: {{ payload.metadata.source_name }}; дата файла: {{ payload.metadata.source_date }}; исполнитель файла: {{ payload.metadata.source_actor }}.</p>
<p>Общие объёмы файла: ДНК {{ payload.metadata.common_dna }} мкл; вода {{ payload.metadata.common_water }} мкл.
Эти сведения не подменяют автора и время аудита, название и настройки блока.</p>
<h2>Будущая схема</h2><div style="overflow-x:auto"><table><thead><tr><th></th>{% for c in columns %}<th>{{ c }}</th>{% endfor %}</tr></thead>
<tbody>{% for row in rows %}<tr><th>{{ row.label }}</th>{% for cell in row.cells %}<td style="min-width:85px;vertical-align:top">{{ cell.label }}<br><small>{{ cell.status }}</small></td>{% endfor %}</tr>{% endfor %}</tbody></table></div>
<h2>Различия</h2><div style="overflow-x:auto"><table><thead><tr><th>Позиция</th><th>Было</th><th>Станет</th><th>Объёмы ДНК / воды, мкл — было → станет</th><th>Примечание — было → станет</th></tr></thead>
<tbody>{% for c in changes %}<tr><td>{{ c.position }}</td><td>{{ c.before_label }}<br>{{ c.before_status }}</td><td>{{ c.after_label }}<br>{{ c.after_status }}</td>
<td>{{ c.before_volumes }} → {{ c.after_volumes }}</td><td>{{ c.before_note }} → {{ c.after_note }}</td></tr>{% empty %}<tr><td colspan="5">Изменений нет.</td></tr>{% endfor %}</tbody></table></div>
{% if error %}<p class="errornote">{{ error }}</p>{% endif %}
<form method="post" action="{{ apply_url }}">{% csrf_token %}<input type="hidden" name="token" value="{{ token }}">
{% if payload.source_unregistered %}<p><label><input type="checkbox" name="ack_raw" value="1" required> Подтверждаю сохранение незарегистрированных обозначений. Карточки проб не создаются.</label></p>{% endif %}
{% if payload.clear %}<p><label><input type="checkbox" name="ack_clear" value="1" required> Подтверждаю очистку позиций, показанных в различиях.</label></p>{% endif %}
<button type="submit">{{ button }}</button></form><p>Срок предпросмотра — 15 минут. Изменение блока потребует нового просмотра.</p>
{% endblock %}"""

LABELS={"sample":"Зарегистрированная проба","water":"Водный контроль","unregistered":"Не зарегистрировано"}
def status(data):return LABELS[data["kind"]] if data else "Пусто"
def volumes(data):return "—" if data is None else data["dna"]+" / "+(data["water"] if data["water"] is not None else "не указано")

class ExcelMixin:
    def get_urls(self):
        return [path("<int:pk>/excel/template/",self.admin_site.admin_view(self.excel_template),name="lab_sourceblock_excel_template"),
                path("<int:pk>/excel/export/",self.admin_site.admin_view(self.excel_export),name="lab_sourceblock_excel_export"),
                path("<int:pk>/excel/import/",self.admin_site.admin_view(self.excel_import),name="lab_sourceblock_excel_import"),
                path("<int:pk>/excel/apply/",self.admin_site.admin_view(self.excel_apply),name="lab_sourceblock_excel_apply")]+super().get_urls()
    def sample_scope(self,request):
        ma=self.admin_site._registry[Sample]
        if not ma.has_view_permission(request):return Sample.objects.none()
        qs=ma.get_queryset(request)
        ids=[obj.pk for obj in qs.iterator(chunk_size=500) if ma.has_view_permission(request,obj)]
        return qs.filter(pk__in=ids)
    def workbook(self,request,pk,template=False):
        if request.method not in {"GET","HEAD"}:return HttpResponseNotAllowed(["GET","HEAD"])
        block=self.get_block(request,pk)
        metadata={"id":block.pk,"revision":block.revision,"name":block.name,"date":timezone.localtime(block.created_at).date().isoformat(),
            "actor":block.created_by.get_username(),"dna":str(block.default_dna_ul),"water":str(block.default_water_ul)}
        try:data=export(metadata,[] if template else [entry(w) for w in block.wells.select_related("sample")],template)
        except ExcelError as exc:
            self.message_user(request,str(exc),messages.ERROR);return HttpResponseRedirect(self.url("layout",pk))
        response=HttpResponse(data,content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        response["Content-Disposition"]='attachment; filename="source-block-%s-%s.xlsx"'%(pk,"template" if template else "export")
        return response
    def excel_template(self,request,pk):return self.workbook(request,pk,True)
    def excel_export(self,request,pk):return self.workbook(request,pk,False)
    def show_preview(self,request,block,receipt,error=""):
        payload=receipt.payload;future={(w.row,w.column):entry(w) for w in block.wells.select_related("sample")}
        changes=[]
        for c in payload["changes"]:
            key=(c["row"],c["column"])
            if c["after"] is None:future.pop(key,None)
            else:future[key]=c["after"]
            before,after=c["before"],c["after"]
            changes.append({"position":f'{c["row"]}{c["column"]}',"before_label":caption(before),"after_label":caption(after),
                "before_status":status(before),"after_status":status(after),"before_volumes":volumes(before),"after_volumes":volumes(after),
                "before_note":before["note"] if before else "—","after_note":after["note"] if after else "—"})
        rows=[{"label":row,"cells":[{"label":caption(future.get((row,col))),"status":status(future.get((row,col)))} for col in range(1,13)]} for row in "ABCDEFGH"]
        return self.render(request,PREVIEW,{"title":"Предпросмотр импорта Excel","plate":block,"receipt":receipt,"payload":payload,
            "changes":changes,"count":len(changes),"rows":rows,"columns":range(1,13),"token":receipt_token(receipt),"error":error,
            "button":"Применить изменения" if changes else "Подтвердить отсутствие изменений","apply_url":self.url("excel_apply",block.pk),
            "upload_url":self.url("excel_import",block.pk),"layout_url":self.url("layout",block.pk)})
    def excel_import(self,request,pk):
        if request.method not in {"GET","HEAD","POST"}:return HttpResponseNotAllowed(["GET","HEAD","POST"])
        block=self.get_block(request,pk,edit=True);form=UploadForm(request.POST if request.method=="POST" else None,request.FILES if request.method=="POST" else None)
        if form.is_valid():
            try:
                scope=self.sample_scope(request)
                receipt=create_preview(form.cleaned_data["file"].read(MAX_BYTES+1),pk,request.user,sample_scope=scope,
                    mode=form.cleaned_data["mode"],clear=form.cleaned_data["clear"])
                block.refresh_from_db();return self.show_preview(request,block,receipt)
            except ValidationError as exc:form.add_error(None," ".join(exc.messages))
        return self.render(request,UPLOAD,{"title":"Импорт исходного блока","plate":block,"form":form,"layout_url":self.url("layout",pk)})
    def excel_apply(self,request,pk):
        if request.method!="POST":return HttpResponseNotAllowed(["POST"])
        block=self.get_block(request,pk,edit=True)
        try:
            receipt=token_receipt(request.POST.get("token",""),request.user,pk)
            result,first=apply_preview(receipt.pk,pk,request.user,sample_scope=self.sample_scope(request),
                acknowledge_raw=request.POST.get("ack_raw")=="1",acknowledge_clear=request.POST.get("ack_clear")=="1")
            self.message_user(request,"Импорт обработан. Изменено позиций: %s."%len(result.payload["changes"]) if first else "Этот импорт уже был обработан; повторных изменений нет.",messages.SUCCESS)
            return HttpResponseRedirect(self.url("layout",pk))
        except ValidationError as exc:
            self.message_user(request," ".join(exc.messages),messages.ERROR)
            return HttpResponseRedirect(self.url("excel_import",pk))
