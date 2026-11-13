from datetime import timedelta
from decimal import Decimal, localcontext, getcontext
from django.contrib import admin
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from lab.test_stage6a import Fixtures
from lab.models import (BufferName,PreparationProtocol,PreparationProtocolComponent,PreparationCalculator,
    Lot,Movement,Preparation,Audit)
from lab.preparation_calculator import calculate

class CalculatorTests(Fixtures,TestCase):
    def setUp(self):
        super().setUp()
        self.product=BufferName.objects.create(name="7A calculator test",kind="buffer",active=True)
        self.protocol=PreparationProtocol.objects.create(product=self.product,title="7A recipe",version="1",base_volume_ml=Decimal("100"),instructions="Approved local recipe")
        self.component=PreparationProtocolComponent.objects.create(protocol=self.protocol,reagent_name="Test component",quantity=Decimal("10"),unit="ml")
        self.lot.unit="мл";self.lot.save()
        self.component.allowed_lots.add(self.lot)
    def calc(self,volume="100"):return calculate(self.protocol.pk,volume)
    def test_volume_scaling(self):
        row=self.calc("250")["rows"][0]
        self.assertEqual(row["required"],Decimal("25"))
        self.assertEqual(row["available"],Decimal("10"))
        self.assertEqual(row["shortage"],Decimal("15"))
    def test_unit_conversion(self):
        self.component.unit="ul";self.component.save()
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("10000"))
    def test_mass_conversion(self):
        self.component.unit="g";self.component.save()
        self.lot.unit="кг";self.lot.quantity=Decimal("1");self.lot.save()
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("1000"))
    def test_no_mass_volume_conversion(self):
        self.lot.unit="г";self.lot.save()
        row=self.calc()["rows"][0]
        self.assertEqual(row["available"],0)
        self.assertFalse(row["candidates"][0]["compatible"])
    def test_zero_stock_not_sent_to_positive_converter(self):
        self.lot.quantity=Decimal("0");self.lot.save()
        row=self.calc()["rows"][0]
        self.assertEqual(row["available"],0)
        self.assertEqual(row["shortage"],Decimal("10"))
    def test_only_allowed_lots(self):
        self.component.allowed_lots.clear()
        row=self.calc()["rows"][0]
        self.assertTrue(row["no_allowed_lots"])
        self.assertEqual(row["candidates"],[])
    def test_reserved_quantity_excluded(self):
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="reserve",qty=Decimal("3"),note="Reserve")
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("7"))
    def test_expired_excluded(self):
        self.lot.expiry=timezone.localdate()-timedelta(days=1);self.lot.save()
        row=self.calc()["rows"][0]
        self.assertEqual(row["available"],0)
        self.assertEqual(row["candidates"][0]["free"],Decimal("10"))
    def test_today_expiry_allowed(self):
        self.lot.expiry=timezone.localdate();self.lot.save()
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("10"))
    def test_unreleased_excluded(self):
        self.lot.released=False;self.lot.save()
        self.assertEqual(self.calc()["rows"][0]["available"],0)
    def test_to_final_volume_not_zero(self):
        self.component.quantity=None;self.component.to_final_volume=True;self.component.save()
        row=self.calc()["rows"][0]
        self.assertIsNone(row["required"])
        self.assertIsNone(row["shortage"])
        self.assertTrue(row["manual_amount"])
    def test_shared_stock_warning(self):
        second=PreparationProtocolComponent.objects.create(protocol=self.protocol,reagent_name="Second",quantity=Decimal("10"),unit="ml")
        second.allowed_lots.add(self.lot)
        result=self.calc()
        self.assertEqual([lot.pk for lot in result["shared_lots"]],[self.lot.pk])
        self.assertTrue(all(row["shared"] for row in result["rows"]))
        self.assertNotIn("all_available",result)
    def test_pending_not_deducted_as_reservation(self):
        self.line(qty="4")
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("10"))
    def test_refreshes_current_stock(self):
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="use_free",qty=Decimal("3"),note="Use")
        self.assertEqual(self.lot.quantity,Decimal("10"))
        self.assertEqual(self.calc()["rows"][0]["available"],Decimal("7"))
    def test_inactive_product_freshly_checked(self):
        BufferName.objects.filter(pk=self.product.pk).update(active=False)
        with self.assertRaises(ValidationError):self.calc()
    def test_invalid_volumes(self):
        for volume in ("0","-1","NaN","Infinity","invalid"):
            with self.assertRaises(ValidationError):self.calc(volume)
    def test_precision_is_explicit_and_context_restored(self):
        self.protocol.base_volume_ml=Decimal("3");self.protocol.save()
        self.component.quantity=Decimal("1");self.component.save()
        previous=getcontext().prec
        row=self.calc("1")["rows"][0]
        with localcontext() as context:
            context.prec=50
            expected=Decimal("1")/Decimal("3")
        self.assertEqual(row["required"],expected)
        self.assertEqual(getcontext().prec,previous)
    def test_get_does_not_change_stock_or_create_preparation(self):
        self.client.force_login(self.executor)
        before=(list(Lot.objects.order_by("pk").values()),Movement.objects.count(),Preparation.objects.count(),Audit.objects.count())
        response=self.client.get(reverse("admin:lab_preparationcalculator_changelist"),{"protocol":self.protocol.pk,"volume_ml":"100","calculate":"1"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(before,(list(Lot.objects.order_by("pk").values()),Movement.objects.count(),Preparation.objects.count(),Audit.objects.count()))
    def test_post_rejected(self):
        self.client.force_login(self.executor)
        self.assertEqual(self.client.post(reverse("admin:lab_preparationcalculator_changelist"),{}).status_code,405)
    def test_write_permissions_disabled(self):
        request=RequestFactory().get("/");request.user=self.leader
        ma=admin.site._registry[PreparationCalculator]
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))
        self.assertFalse(ma.has_delete_permission(request))
    def test_protocol_link_added(self):
        ma=admin.site._registry[PreparationProtocol]
        self.assertIn("calculator_link",ma.readonly_fields)
        self.assertIn("calculator_link",ma.fields)
        self.assertIn("calculator_link",ma.list_display)
    def test_empty_recipe_rejected(self):
        self.component.allowed_lots.clear();self.component.delete()
        with self.assertRaises(ValidationError):self.calc()
