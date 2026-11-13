"""
Ядро приложения lab.
Связывает чистые доменные папки отделов (warehouse, solutions, dna_extraction,
electrophoresis, samples, worklog, users, api) с пространством имён `lab.*` для
полной обратной совместимости всех миграций и внутренних вызовов.
"""
import importlib
import sys

_DEPARTMENT_MODULES = {
    # warehouse (Склад и хранение)
    "stock_entry": "warehouse.modules.stock_entry",
    "stock_availability": "warehouse.modules.availability",
    "storage_support": "warehouse.modules.hierarchy",
    "storage_stage4_admin": "warehouse.modules.hierarchy_admin",
    "storage8d": "warehouse.modules.block_storage",
    "storage8d_admin": "warehouse.modules.block_storage_admin",
    "storage8d_support": "warehouse.modules.block_storage_utils",
    "storage8e_admin": "warehouse.modules.contents_admin",
    "storage8z": "warehouse.modules.placement",
    "storage8z_admin": "warehouse.modules.placement_admin",
    "storage8zh_admin": "warehouse.modules.units_display_admin",
    "storage8zh_support": "warehouse.modules.units_display",
    # solutions (Приготовление растворов и автоклав)
    "preparation_access": "solutions.modules.permissions",
    "preparation_allocation": "solutions.modules.allocation",
    "preparation_calculator": "solutions.modules.calculator",
    "preparation_drafts": "solutions.modules.drafts",
    "preparation_drafts_admin": "solutions.modules.drafts_admin",
    "preparation_list7d": "solutions.modules.list_filters",
    "preparation_readiness": "solutions.modules.readiness",
    "preparation_readiness_admin": "solutions.modules.readiness_admin",
    "preparation_stage2_admin": "solutions.modules.autoclaving_admin",
    "preparation_wizard": "solutions.modules.wizard",
    # dna_extraction (Выделение ДНК и ПЦР)
    "lab6b2_admin": "dna_extraction.admin",
    "lab6b2_services": "dna_extraction.services",
    # samples (Образцы, паспорта ВИР и планшеты 96 лунок)
    "sample_batch": "samples.modules.batch",
    "sample_dates": "samples.modules.collection_dates",
    "sample_projects8a": "samples.modules.projects",
    "sample_projects8a_admin": "samples.modules.projects_admin",
    "sample_series_stage5": "samples.modules.series_index",
    "sample_stage5b_admin": "samples.modules.collection_admin",
    "material_passport_admin": "samples.modules.passport_admin",
    "passport_excel": "samples.modules.passport_excel",
    "source_blocks8v": "samples.modules.plates",
    "source_blocks8v_admin": "samples.modules.plates_admin",
    "source_blocks8v_models": "samples.modules.plates_models",
    "source_blocks8g": "samples.modules.plates_import",
    "source_blocks8g_admin": "samples.modules.plates_import_admin",
    "source_blocks8g_excel": "samples.modules.plates_excel",
    # worklog (Проекты, задачи, журнал работ и отчёты)
    "worklog6": "worklog.modules.expenses",
    "worklog6_admin": "worklog.modules.expenses_admin",
    "worklog6_permissions": "worklog.modules.permissions",
    "task_samples": "worklog.modules.task_samples",
    "reports": "worklog.modules.reports",
    "admin_navigation": "worklog.modules.navigation",
    "navigation8b": "worklog.modules.sidebar",
    "presentation": "worklog.modules.formatting",
    # tests aliases
    "test_stage6a": "worklog.tests.test_stage_expenses",
    "test_stage7v": "solutions.tests.test_stage_drafts",
    "test_ui_stage1": "worklog.tests.test_ui_navigation",
    "test_preparation_stage2": "solutions.tests.test_preparation_autoclaving",
    "test_preparation_wizard": "solutions.tests.test_preparation_wizard",
    "test_storage_stage4": "warehouse.tests.test_storage_hierarchy",
    "test_storage_upgrade": "warehouse.tests.test_storage_upgrade",
    "test_sample_series_stage5": "samples.tests.test_sample_series_series",
    # api & settings
    "api_views": "api.views",
    "settings": "backend.settings",
    "urls": "backend.urls",
    "wsgi": "backend.wsgi",
}


class _LabModuleFinder:
    @classmethod
    def find_spec(cls, fullname, path=None, target=None):
        if not fullname.startswith("lab."):
            return None
        subname = fullname.split(".", 1)[1]
        if subname in _DEPARTMENT_MODULES:
            return importlib.machinery.ModuleSpec(fullname, cls)
        return None

    @classmethod
    def create_module(cls, spec):
        subname = spec.name.split(".", 1)[1]
        target_mod = importlib.import_module(_DEPARTMENT_MODULES[subname])
        sys.modules[spec.name] = target_mod
        return target_mod

    @classmethod
    def exec_module(cls, module):
        pass


if not any(getattr(f, "__name__", "") == "_LabModuleFinder" for f in sys.meta_path):
    sys.meta_path.insert(0, _LabModuleFinder)


def __getattr__(name):
    if name in _DEPARTMENT_MODULES:
        mod = importlib.import_module(_DEPARTMENT_MODULES[name])
        sys.modules[f"lab.{name}"] = mod
        globals()[name] = mod
        return mod
    raise AttributeError(f"module 'lab' has no attribute {name!r}")
