from types import MethodType
from lab.models import StoragePlace
from lab.storage8zh_support import display_quantity


def install(site):
    if getattr(site, "_storage8zh_installed", False):
        return
    if not getattr(site, "_storage8e_installed", False):
        raise RuntimeError("8ZH requires installed 8E")
    from lab import storage8e_admin
    storage8e_admin.COLUMNS["lots"] = tuple(
        (key, "Единица учёта" if key == "unit" else label)
        for key, label in storage8e_admin.COLUMNS["lots"]
    )
    modeladmin = site._registry[StoragePlace]
    original = modeladmin.contents_view
    def contents(self, request, object_id):
        response = original(request, object_id)
        if not hasattr(response, "context_data"):
            return response
        if response.is_rendered:
            raise RuntimeError("8ZH expected unrendered TemplateResponse")
        for section in response.context_data["storage8e_sections"]:
            if section["key"] != "lots":
                continue
            columns = section["columns"]
            for obj, row in zip(section["page"].object_list, section["rows"], strict=True):
                for (key, label), cell in zip(columns, row, strict=True):
                    if key in {"quantity", "reserved", "free"}:
                        cell["value"] = display_quantity(getattr(obj, key), obj.unit)
        return response
    modeladmin.contents_view = MethodType(contents, modeladmin)
    site._storage8zh_installed = True
