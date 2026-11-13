from types import MethodType
from django.core.exceptions import PermissionDenied
from django.http import Http404,HttpResponseNotAllowed
from django.template.response import TemplateResponse
from django.urls import path,reverse
from lab.admin_navigation import GROUPS

SECTION_KEYS={item[0] for item in GROUPS}|{"lab_other"}

def install(site):
    if getattr(site,"_navigation8b_installed",False):return
    if not getattr(site,"_lab_ui_stage1",False):raise RuntimeError("Existing grouped navigation required")
    old_apps,old_context,old_urls=site.get_app_list,site.each_context,site.get_urls
    site._navigation8b_previous_get_app_list=old_apps
    def app_list(self,request,app_label=None):
        real_label="lab" if app_label in SECTION_KEYS else app_label
        apps=old_apps(request,real_label)
        result=[]
        for app in apps:
            label=app["app_label"]
            if label in SECTION_KEYS or label=="lab":
                key="lab_other" if label=="lab" else label
                updated={**app,"app_label":key,"app_url":reverse(self.name+":lab_section8b",args=[key]),
                    "models":[dict(model) for model in app["models"]]}
                result.append(updated)
            else:result.append({**app,"models":[dict(model) for model in app["models"]]})
        return [app for app in result if app["app_label"]==app_label] if app_label in SECTION_KEYS else result
    site.get_app_list=MethodType(app_list,site)
    def active_group(self,request,apps):
        match=getattr(request,"resolver_match",None)
        if match is None:return None,""
        url_name=match.url_name or ""
        if url_name=="lab_section8b":
            key=match.kwargs.get("section")
            return next((app for app in apps if app["app_label"]==key),None),""
        if url_name=="app_list":
            key=match.kwargs.get("app_label")
            return next((app for app in apps if app["app_label"]==key),None),""
        for model in sorted(self._registry,key=lambda model:len(model._meta.model_name),reverse=True):
            prefix=model._meta.app_label+"_"+model._meta.model_name+"_"
            if not url_name.startswith(prefix):continue
            for app in apps:
                for item in app["models"]:
                    if item["object_name"]==model._meta.object_name and (app["app_label"] in SECTION_KEYS if model._meta.app_label=="lab" else app["app_label"]==model._meta.app_label):
                        return app,item.get("admin_url","")
        return None,""
    def each_context(self,request):
        context=old_context(request)
        apps=context.get("available_apps",[])
        active,model_url=active_group(self,request,apps)
        return {**context,"lab8b_sidebar_apps":apps,
            "lab8b_section_name":active["name"] if active else "",
            "lab8b_section_key":active["app_label"] if active else "",
            "lab8b_section_url":active["app_url"] if active else "",
            "lab8b_active_model_url":model_url,"lab8b_lab_url":reverse(self.name+":app_list",args=["lab"])}
    site.each_context=MethodType(each_context,site)
    def section_view(self,request,section):
        if request.method not in {"GET","HEAD"}:return HttpResponseNotAllowed(["GET","HEAD"])
        if section not in SECTION_KEYS:raise Http404
        available=self.get_app_list(request,section)
        if not available:raise PermissionDenied
        item=available[0]
        request.current_app=self.name
        context={**self.each_context(request),"title":item["name"],"subtitle":None,
            "section":item,"section_apps":[item],"lab8b_sidebar_apps":self.get_app_list(request),
            "lab8b_section_name":item["name"],"lab8b_section_key":section,"lab8b_section_url":item["app_url"]}
        return TemplateResponse(request,"admin/lab/navigation8b/section.html",context)
    site.section_view8b=MethodType(section_view,site)
    def urls(self):
        return [path("lab/sections/<slug:section>/",self.admin_view(self.section_view8b),name="lab_section8b")]+old_urls()
    site.get_urls=MethodType(urls,site)
    site.enable_nav_sidebar=True
    site._navigation8b_full_menu=True
    site._navigation8b_installed=True
