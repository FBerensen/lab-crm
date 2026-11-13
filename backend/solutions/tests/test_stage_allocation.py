from decimal import Decimal
from datetime import timedelta
from django.test import TestCase,RequestFactory
from django.contrib import admin
from django.core.exceptions import ValidationError,PermissionDenied
from django.urls import reverse
from django.utils import timezone
from lab.test_stage6a import Fixtures
from lab.models import (BufferName,PreparationProtocol,PreparationProtocolComponent,PreparationCalculator,
    Lot,Movement,Preparation,Audit)
from lab.preparation_allocation import validate_selection,pair_names

class AllocationTests(Fixtures,TestCase):
    def setUp(self):
        super().setUp()
        self.product=BufferName.objects.create(name="7B test",kind="buffer",active=True)
        self.protocol=PreparationProtocol.objects.create(product=self.product,title="7B recipe",version="1",base_volume_ml=Decimal("100"),instructions="Approved local recipe")
        self.component=PreparationProtocolComponent.objects.create(protocol=self.protocol,reagent_name="First",quantity=Decimal("10"),unit="ml")
        self.lot.unit="мл";self.lot.save()
        self.component.allowed_lots.add(self.lot)
    def entry(self,qty="10",component=None,lot=None):
        return {"component_id":(component or self.component).pk,"lot_id":(lot or self.lot).pk,"qty":qty}
    def validate(self,entries):return validate_selection(self.protocol.pk,"100",entries)
    def post_data(self,qty="10"):
        take,name=pair_names(self.component.pk,self.lot.pk)
        return {"protocol":str(self.protocol.pk),"volume_ml":"100",take:"on",name:qty}
    def test_exact_selection_valid(self):
        result=self.validate([self.entry()]);self.assertTrue(result["valid"])
        self.assertEqual(result["totals"][0]["qty"],Decimal("10"))
    def test_split_component_between_lots(self):
        other=Lot.objects.create(name="Second lot",unit="мл",quantity=Decimal("5"),initial_quantity=0)
        self.component.allowed_lots.add(other)
        self.assertTrue(self.validate([self.entry("5"),self.entry("5",lot=other)])["valid"])
    def test_shared_lot_total_checked(self):
        other=PreparationProtocolComponent.objects.create(protocol=self.protocol,reagent_name="Second",quantity=Decimal("10"),unit="ml")
        other.allowed_lots.add(self.lot)
        result=self.validate([self.entry(),self.entry(component=other)])
        self.assertFalse(result["valid"])
        self.assertEqual(result["totals"][0]["qty"],Decimal("20"))
        self.assertEqual(result["totals"][0]["excess"],Decimal("10"))
    def test_shared_lot_feasible_when_totals_fit(self):
        self.component.quantity=Decimal("5");self.component.save()
        other=PreparationProtocolComponent.objects.create(protocol=self.protocol,reagent_name="Second",quantity=Decimal("5"),unit="ml")
        other.allowed_lots.add(self.lot)
        self.assertTrue(self.validate([self.entry("5"),self.entry("5",component=other)])["valid"])
    def test_shortage_not_rounded_away(self):
        result=self.validate([self.entry("9.9999")]);self.assertFalse(result["valid"])
        self.assertIn("0.0001",result["rows"][0]["shortage_text"])
    def test_overage_not_implicitly_allowed(self):
        self.lot.quantity=Decimal("20");self.lot.save()
        result=self.validate([self.entry("10.0001")]);self.assertFalse(result["valid"])
        self.assertIn("0.0001",result["rows"][0]["excess_text"])
    def test_unit_conversion_uses_stock_unit(self):
        self.component.quantity=Decimal("10000");self.component.unit="ul";self.component.save()
        result=self.validate([self.entry("10")]);self.assertTrue(result["valid"])
        self.assertEqual(result["rows"][0]["selected_text"],"10000")
    def test_duplicate_pair_rejected(self):
        with self.assertRaises(ValidationError):self.validate([self.entry("5"),self.entry("5")])
    def test_unallowed_lot_rejected(self):
        other=Lot.objects.create(name="Unallowed",unit="мл",quantity=Decimal("20"),initial_quantity=0)
        with self.assertRaises(ValidationError):self.validate([self.entry(lot=other)])
    def test_inactive_recipe_rejected(self):
        self.protocol.active=False;self.protocol.save()
        with self.assertRaises(ValidationError):self.validate([self.entry()])
    def test_stock_is_rechecked(self):
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="use_free",qty=Decimal("2"),note="Use after display")
        result=self.validate([self.entry()]);self.assertFalse(result["valid"])
        self.assertEqual(result["totals"][0]["free"],Decimal("8"))
    def test_reserved_stock_not_available(self):
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="reserve",qty=Decimal("3"),note="Reserve")
        self.assertFalse(self.validate([self.entry()])["valid"])
    def test_expired_lot_rejected(self):
        self.lot.expiry=timezone.localdate()-timedelta(days=1);self.lot.save()
        with self.assertRaises(ValidationError):self.validate([self.entry()])
    def test_unreleased_lot_rejected(self):
        self.lot.released=False;self.lot.save()
        with self.assertRaises(ValidationError):self.validate([self.entry()])
    def test_quantity_precision_and_finiteness(self):
        for qty in ("0","-1","NaN","Infinity","1.00001"):
            with self.assertRaises(ValidationError):self.validate([self.entry(qty)])
    def test_unknown_solvent_quantity_is_not_zero(self):
        self.component.quantity=None;self.component.to_final_volume=True;self.component.save()
        result=self.validate([self.entry(None)])
        self.assertTrue(result["fixed_valid"]);self.assertFalse(result["complete"])
        self.assertEqual(result["totals"][0]["unknown_count"],1)
        self.assertIsNone(result["selections"][0]["qty"])
        self.assertEqual(result["rows"][0]["selected_text"],"Не определено")
    def test_manual_solvent_quantity_can_be_checked_against_stock(self):
        self.component.quantity=None;self.component.to_final_volume=True;self.component.save()
        result=self.validate([self.entry("4")]);self.assertTrue(result["complete"])
        self.assertTrue(result["warnings"])
    def test_fixed_quantity_required(self):
        with self.assertRaises(ValidationError):self.validate([self.entry(None)])
    def test_get_post_do_not_write_application_data(self):
        self.client.force_login(self.student)
        before=(list(Lot.objects.order_by("pk").values()),Movement.objects.count(),Preparation.objects.count(),Audit.objects.count())
        url=reverse("admin:lab_preparationcalculator_allocation")
        self.assertEqual(self.client.get(url,{"protocol":self.protocol.pk,"volume_ml":"100"}).status_code,200)
        self.assertEqual(self.client.post(url,self.post_data()).status_code,200)
        self.assertEqual(before,(list(Lot.objects.order_by("pk").values()),Movement.objects.count(),Preparation.objects.count(),Audit.objects.count()))
    def test_forged_row_is_form_error(self):
        self.client.force_login(self.executor)
        data=self.post_data();data["qty_999999_999999"]="1"
        response=self.client.post(reverse("admin:lab_preparationcalculator_allocation"),data)
        self.assertEqual(response.status_code,200)
        self.assertContains(response,"неизвестная строка")
    def test_nonstaff_denied(self):
        self.executor.is_staff=False
        request=RequestFactory().get("/");request.user=self.executor
        with self.assertRaises(PermissionDenied):admin.site._registry[PreparationCalculator].allocation_view(request)
    def test_7a_link_present(self):
        self.client.force_login(self.executor)
        response=self.client.get(reverse("admin:lab_preparationcalculator_changelist"),{"protocol":self.protocol.pk,"volume_ml":"100","calculate":"1"})
        self.assertContains(response,reverse("admin:lab_preparationcalculator_allocation"))
