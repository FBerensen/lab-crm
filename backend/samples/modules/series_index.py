from types import MethodType
from urllib.parse import urlencode
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.template.response import TemplateResponse
from django.urls import path, reverse


def series_summary(queryset, query=""):
    rows = queryset.order_by().values("series").annotate(
        samples=Count("pk", distinct=True), projects=Count("projects__pk", distinct=True),
        places=Count("storage_place_id", distinct=True),
        unplaced=Count("pk", filter=Q(storage_place__isnull=True), distinct=True),
    ).order_by("series")
    if query:
        rows = rows.filter(series__icontains=query)
    return rows


def configure_admin(site):
    if getattr(site, "_lab_sample_series_stage5", False):
        return
    from lab.models import Sample
    modeladmin = site._registry[Sample]
    original_urls = modeladmin.get_urls
    original_changelist = modeladmin.changelist_view

    def series_view(self, request):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        if request.method != "GET":
            from django.http import HttpResponseNotAllowed
            return HttpResponseNotAllowed(["GET"])
        base = self.get_queryset(request)
        query = request.GET.get("q", "").strip()
        summary = series_summary(base, query)
        page = Paginator(summary, 50).get_page(request.GET.get("page"))
        rows = list(page.object_list)
        names = [row["series"] for row in rows]
        projects = {}
        places = {}
        for series, pk, title in base.filter(series__in=names).order_by("series", "projects__pk").values_list("series", "projects__pk", "projects__title").distinct():
            projects.setdefault(series, []).append({"title": title, "id": pk})
        for series, pk, name in base.filter(series__in=names, storage_place__isnull=False).order_by("series", "storage_place_id").values_list("series", "storage_place_id", "storage_place__name").distinct():
            places.setdefault(series, []).append({"name": name, "id": pk})
        sample_list = reverse("admin:lab_sample_changelist")
        for row in rows:
            row["url"] = sample_list + "?" + urlencode({"series__exact": row["series"]})
            row["project_names"] = projects.get(row["series"], [])[:3]
            row["extra_projects"] = max(0, row["projects"] - 3)
            row["place_names"] = places.get(row["series"], [])[:3]
            row["extra_places"] = max(0, row["places"] - 3)
        def page_url(number):
            params = request.GET.copy()
            params["page"] = number
            return reverse("admin:lab_sample_series") + "?" + params.urlencode()
        return TemplateResponse(request, "admin/lab/sample/series_index.html", {
            **self.admin_site.each_context(request), "title": "Серии образцов и проб", "opts": self.model._meta,
            "rows": rows, "page": page, "q": query, "total_samples": base.count(),
            "total_series": series_summary(base).count(), "series_url": reverse("admin:lab_sample_series"),
            "all_samples_url": sample_list + "?all_samples=1",
            "previous_url": page_url(page.previous_page_number()) if page.has_previous() else None,
            "next_url": page_url(page.next_page_number()) if page.has_next() else None,
        })

    def changelist(self, request, extra_context=None):
        if not self.has_view_or_change_permission(request):
            raise PermissionDenied
        params = request.GET.copy()
        force_flat = params.get("all_samples") == "1"
        params.pop("all_samples", None)
        if request.method == "GET" and not params and not force_flat:
            return self.series_index_view(request)
        request.GET = params
        context = dict(extra_context or {})
        selected_series = params.get("series__exact")
        if selected_series is not None:
            context["title"] = "Образцы серии «" + selected_series + "»"
        response = original_changelist(request, extra_context=context)
        # Add the navigation marker only AFTER Django validates ordinary filters.
        # Keep flat mode on pagination, sorting and search links without treating it as an ORM lookup.
        result_context = getattr(response, "context_data", None)
        if result_context and "_popup" not in params:
            cl = result_context.get("cl")
            if cl is not None:
                cl.params["all_samples"] = "1"
                if hasattr(cl, "filter_params"):
                    cl.filter_params["all_samples"] = ["1"]
        return response

    def urls(self):
        return [path("series/", self.admin_site.admin_view(self.series_index_view), name="lab_sample_series")] + original_urls()

    modeladmin.series_index_view = MethodType(series_view, modeladmin)
    modeladmin.changelist_view = MethodType(changelist, modeladmin)
    modeladmin.get_urls = MethodType(urls, modeladmin)
    site._lab_sample_series_stage5 = True
