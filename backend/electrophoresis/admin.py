from django.contrib import admin
from lab.admin import CaptureAdmin, export_csv
from lab.models import lead
from .models import ElectrophoresisGel, ElectrophoresisLane


class ElectrophoresisLaneInline(admin.TabularInline):
    model = ElectrophoresisLane
    extra = 4
    fields = ("lane_number", "sample", "is_control", "sample_volume_ul", "band_size_bp", "quality", "note")


class ElectrophoresisGelAdmin(CaptureAdmin):
    list_display = ("id", "actual_day", "task", "agarose_percent", "voltage_v", "duration_min", "status", "actor")
    list_filter = ("status", "actual_day", "agarose_percent")
    search_fields = ("task__title", "marker_name", "result_summary")
    readonly_fields = ("actor", "created_at")
    inlines = (ElectrophoresisLaneInline,)
    actions = (export_csv,)

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_staff

    def has_change_permission(self, request, obj=None):
        if not request.user.is_active or not request.user.is_staff:
            return False
        if obj is None:
            return True
        return obj.status == ElectrophoresisGel.Status.DRAFT and (obj.actor_id == request.user.pk or lead(request.user))


def install(site):
    if not site.is_registered(ElectrophoresisGel):
        site.register(ElectrophoresisGel, ElectrophoresisGelAdmin)
