from decimal import Decimal
from types import SimpleNamespace as NS
from unittest import TestCase
from lab.presentation import compact_number, preparation_label, lot_label
from lab.admin_navigation import group_app_list
from datetime import date


class PresentationTests(TestCase):
    def test_numbers(self):
        for value, expected in (("100.0000", "100"), ("250.0000", "250"), ("0.5000", "0.5"), ("0.0001", "0.0001"), ("100", "100"), ("-0.0000", "0"), ("-1.2500", "-1.25")):
            with self.subTest(value=value):
                self.assertEqual(compact_number(Decimal(value)), expected)

    def test_missing(self):
        self.assertEqual(compact_number(None), "—")

    def preparation(self, **changes):
        p = NS(output_id=1, output=NS(name="TAE 50×", unit="мл"), recipe_id=None, actual_day=date(2026, 11, 4), processed=True, actual_volume_ml=Decimal("250.0000"), qty=Decimal("250"), planned_volume_ml=Decimal("500"), actor_id=1, actor=NS(get_full_name=lambda: "Иван Иванов", get_username=lambda: "ivan"), protocol="old", kind="Приготовление реактива")
        p.__dict__.update(changes)
        return p

    def test_prepared_label(self):
        self.assertEqual(preparation_label(self.preparation()), "TAE 50× · 04.11.2026 · 250 мл · Иван Иванов")

    def test_plan_not_presented_as_fact(self):
        self.assertIn("500 мл (план)", preparation_label(self.preparation(processed=False)))

    def test_historical_volume_uses_original_unit(self):
        self.assertIn("2 л", preparation_label(self.preparation(actual_volume_ml=None, qty=Decimal("2"), output=NS(name="Буфер", unit="л"))))

    def test_username_fallback(self):
        p = self.preparation(actor=NS(get_full_name=lambda: "", get_username=lambda: "ivan"))
        self.assertTrue(preparation_label(p).endswith("ivan"))

    def test_missing_actor(self):
        self.assertIn("исполнитель не указан", preparation_label(self.preparation(actor_id=None)))

    def test_ordinary_lot(self):
        self.assertEqual(lot_label(NS(pk=1, name="NaCl", code="STK-1", unit="г")), "NaCl · STK-1 [г]")

    def test_prepared_lot(self):
        self.assertIn("250 мл", lot_label(NS(pk=1, production_record=self.preparation())))


class NavigationTests(TestCase):
    def app(self, names):
        return {"app_label": "lab", "name": "Lab", "app_url": "/admin/lab/", "has_module_perms": True, "models": [{"object_name": name, "name": name, "perms": {"view": True}, "admin_url": "/admin/lab/" + name.lower() + "/"} for name in names]}

    def test_business_order(self):
        groups = group_app_list([self.app(["Preparation", "Sample", "Task", "Project", "Movement"])])
        self.assertEqual([g["app_label"] for g in groups], ["lab_work", "lab_samples", "lab_stock", "lab_preparation"])
        self.assertEqual([m["object_name"] for m in groups[0]["models"]], ["Project", "Task"])

    def test_no_extra_permissions_or_models(self):
        app = self.app(["Sample"])
        before = repr(app)
        groups = group_app_list([app])
        self.assertEqual(len(groups), 1)
        self.assertIs(groups[0]["models"][0], app["models"][0])
        self.assertEqual(repr(app), before)

    def test_other_app_preserved(self):
        auth = {"app_label": "auth", "models": []}
        self.assertIs(group_app_list([auth])[0], auth)

    def test_new_model_not_lost(self):
        groups = group_app_list([self.app(["FutureModel"])])
        self.assertEqual(groups[0]["models"][0]["object_name"], "FutureModel")

    def test_empty_hidden(self):
        self.assertEqual(group_app_list([self.app([])]), [])
