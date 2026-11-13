from types import MethodType
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse,HttpResponseNotAllowed
from django.shortcuts import get_object_or_404
from django.template import engines
from django.urls import path,reverse
from django.utils.html import format_html
from lab.models import Preparation
from lab.preparation_readiness import inspect_preparation

PAGE="""
{% extends "admin/base_site.html" %}{% block content %}
<h1>Готовность приготовления №{{ p.pk }}</h1><h2>{{ report.status }}</h2>
<p>Проверено: {{ report.checked_at }}. Это информационная предпроверка без списания и резерва.
Данные могут меняться во время проверки. Окончательное решение принимает существующее проведение с повторной проверкой и блокировками.</p>
<p><a href="{{ card }}">К карточке приготовления</a> | <a href="{{ self_url }}">Обновить предпроверку</a></p>
{% if report.processed %}<p>Повторное проведение запрещено. {{ report.release_status }}.
Эта страница не проверяет готовность к допуску и не заменяет проверку истории автоклавирования.</p>
{% else %}
<p>Право текущего пользователя на проведение: {% if can_complete %}есть{% else %}нет{% endif %}.
Это право проверено существующим has_change_permission; готовность данных сама по себе не даёт полномочий.</p>
<p>Протокол: {{ p.recipe|default:"Не указан" }}. План: {{ p.planned_volume_ml|default_if_none:"Не указан" }} мл.
Факт: {{ p.actual_volume_ml|default_if_none:"Не указан" }} мл. Дата выполнения: {{ p.actual_day }}.</p>
<table><thead><tr><th>Проверка</th><th>Результат</th><th>Подробности</th></tr></thead><tbody>
{% for item in report.checks %}<tr><td>{{ item.title }}</td><td>{% if item.ok %}Пройдена{% else %}Требует исправления{% endif %}</td>
<td>{% for error in item.errors %}<div>{{ error }}</div>{% endfor %}<small>{{ item.detail }}</small></td></tr>{% endfor %}</tbody></table>
<h2>Общий фактический расход по партиям</h2>
<table><tr><th>Партия</th><th>Всего расход</th><th>Свободно сейчас</th><th>Препятствия</th></tr>
{% for row in report.totals %}<tr><td>{{ row.lot }}</td><td>{{ row.total }} {{ row.unit }}</td><td>{{ row.free }} {{ row.unit }}</td>
<td>{% for error in row.errors %}<div>{{ error }}</div>{% empty %}Нет{% endfor %}</td></tr>{% empty %}<tr><td colspan="4">Фактический расход пока не введён.</td></tr>{% endfor %}</table>
<p>Расчётный срок готовой партии: {{ report.effective_expiry|default_if_none:"Не задан; автоматический срок не определён" }}.</p>
{% if report.ready and can_complete %}<p>Можно перейти к существующему действию «Провести приготовление и оприходовать результат» в списке приготовлений. На этой странице кнопки списания нет.</p>{% endif %}
<p>Статус готовности не является научным одобрением рецептуры или отклонений от СОП. План и факт не подменяются друг другом.
Допуск готовой партии и подтверждение автоклавирования выполняются отдельно по прежним правилам.</p>{% endif %}
{% endblock %}
"""

def readiness_view(self,request,object_id):
    if request.method!="GET":return HttpResponseNotAllowed(["GET"])
    p=get_object_or_404(Preparation,pk=object_id)
    if not self.has_view_permission(request,p):raise PermissionDenied
    report=inspect_preparation(p.pk);p=report["preparation"]
    context={**self.admin_site.each_context(request),"opts":Preparation._meta,"title":"Готовность приготовления",
        "p":p,"report":report,"can_complete":self.has_change_permission(request,p),
        "card":reverse("admin:lab_preparation_change",args=[p.pk]),"self_url":reverse("admin:lab_preparation_readiness",args=[p.pk])}
    return HttpResponse(engines["django"].from_string(PAGE).render(context,request))

def install(site):
    ma=site._registry[Preparation]
    if getattr(ma,"_readiness7g_installed",False):return
    if not getattr(ma,"_drafts7v_installed",False):raise RuntimeError("7V required")
    previous_urls=ma.get_urls
    ma.readiness_view=MethodType(readiness_view,ma)
    def urls(self):
        return [path("<int:object_id>/readiness/",site.admin_view(self.readiness_view),name="lab_preparation_readiness")]+previous_urls()
    def link(self,obj):
        if obj is None or not obj.pk:return "Сохраните запись для предпроверки."
        return format_html('<a href="{}">Предпроверка: факт, партии и готовность к проведению</a>',reverse("admin:lab_preparation_readiness",args=[obj.pk]))
    link.short_description="Готовность к проведению"
    ma.readiness_link=MethodType(link,ma);ma.get_urls=MethodType(urls,ma)
    ma.readonly_fields=(*ma.readonly_fields,"readiness_link")
    if ma.fields is None:raise RuntimeError("Expected explicit PreparationAdmin.fields")
    ma.fields=("readiness_link",*ma.fields)
    ma._readiness7g_installed=True
