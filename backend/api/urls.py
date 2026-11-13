from django.urls import path
from . import views

urlpatterns = [
    path("overview/", views.api_overview, name="api_overview"),
    path("warehouse/", views.api_warehouse, name="api_warehouse"),
    path("solutions/", views.api_solutions, name="api_solutions"),
    path("dna-extraction/", views.api_dna_extraction, name="api_dna_extraction"),
    path("electrophoresis/", views.api_electrophoresis, name="api_electrophoresis"),
    path("samples/", views.api_samples, name="api_samples"),
    path("worklog/", views.api_worklog, name="api_worklog"),
]
