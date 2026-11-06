import re
from decimal import Decimal
from unittest.mock import patch
from django.db import connection
from django.test import TestCase
from django.urls import reverse
from .test_stage7v import Data
from .models import Preparation,Component,Lot,Movement,Audit,PreparationProtocolComponent
from .preparation_readiness import inspect_preparation

class ReadinessTests(Data,TestCase):
    def errors(self,result):return " ".join(e for row in result["checks"] for e in row["errors"])
    def ready(self):
        p=self.draft();self.fill(p);self.confirm(p);return p
    def test_incomplete_draft_blocked(self):
        result=inspect_preparation(self.draft().pk)
        self.assertFalse(result["ready"])
        self.assertIn("фактический",self.errors(result).lower())
    def test_confirmed_draft_ready(self):
        result=inspect_preparation(self.ready().pk)
        self.assertTrue(result["ready"],self.errors(result))
    def test_readonly_sql_and_no_posting_calls(self):
        p=self.ready()
        def select_only(execute,sql,params,many,context):
            # 7G_FIX1_ALLOW_DJANGO_SAVEPOINTS
            statement=sql.strip().upper()
            control=re.fullmatch(r'(?:SAVEPOINT|RELEASE SAVEPOINT|ROLLBACK TO SAVEPOINT) "[A-Z0-9_]+"',statement)
            self.assertTrue(bool(re.match(r"SELECT\b",statement)) or bool(control),sql)
            return execute(sql,params,many,context)
        with patch.object(Preparation,"complete",side_effect=AssertionError("complete called")),patch.object(Movement,"save",side_effect=AssertionError("movement saved")),patch.object(Lot,"save",side_effect=AssertionError("lot saved")),connection.execute_wrapper(select_only):
            result=inspect_preparation(p.pk)
        self.assertTrue(result["ready"],self.errors(result))
    def test_stale_facts_blocked(self):
        p=self.ready();entry=p.components.get();entry.qty=Decimal("9");entry.save()
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("подтвержден",self.errors(result).lower())
    def test_stock_change_seen_fresh(self):
        p=self.ready()
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="use_free",qty=Decimal("2"),note="Other work")
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("Недостаточно",self.errors(result))
    def test_total_lot_consumption_summed_across_components(self):
        p=self.draft();self.fill(p,qty="6")
        second=PreparationProtocolComponent.objects.create(protocol=self.recipe,position=20,reagent_name="Second",quantity=Decimal("6"),unit="ml")
        second.allowed_lots.add(self.lot)
        Component.objects.create(preparation=p,recipe_component=second,lot=self.lot,qty=Decimal("6"))
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertEqual(result["totals"][0]["total"],Decimal("12"))
        self.assertIn("Недостаточно",self.errors(result))
    def test_forbidden_lot_explained(self):
        p=self.ready();self.component.allowed_lots.clear()
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("не разрешена",self.errors(result))
    def test_missing_solvent_fact_explained(self):
        p=self.draft();self.fill(p)
        PreparationProtocolComponent.objects.create(protocol=self.recipe,position=20,reagent_name="Water",to_final_volume=True,quantity=None,unit="ml")
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("Water",self.errors(result))
    def test_inactive_metadata_explained(self):
        p=self.ready();self.category.active=False;self.category.save()
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("неактивна",self.errors(result))
    def test_existing_output_history_blocks(self):
        p=self.ready();p.output=self.lot;p.save(update_fields=["output"])
        result=inspect_preparation(p.pk)
        self.assertFalse(result["ready"]);self.assertIn("нулевые",self.errors(result))
    def test_processed_does_not_recheck_consumed_sources(self):
        p=self.ready();p.complete()
        result=inspect_preparation(p.pk)
        self.assertTrue(result["processed"]);self.assertFalse(result["ready"])
        self.assertEqual(result["checks"],[])
    def test_view_get_does_not_write_lab_data(self):
        p=self.ready();self.client.force_login(self.executor)
        before=(Preparation.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance())
        response=self.client.get(reverse("admin:lab_preparation_readiness",args=[p.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(before,(Preparation.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance()))
    def test_view_rejects_post(self):
        p=self.ready();self.client.force_login(self.executor)
        response=self.client.post(reverse("admin:lab_preparation_readiness",args=[p.pk]),{})
        self.assertEqual(response.status_code,405)
    def test_read_access_follows_existing_permission_without_change_grant(self):
        from django.contrib import admin
        from django.test import RequestFactory
        p=self.ready();self.client.force_login(self.student)
        request=RequestFactory().get("/");request.user=self.student
        ma=admin.site._registry[Preparation]
        allowed=ma.has_view_permission(request,p)
        self.assertFalse(ma.has_change_permission(request,p))
        response=self.client.get(reverse("admin:lab_preparation_readiness",args=[p.pk]))
        self.assertEqual(response.status_code,200 if allowed else 403)
        if allowed:self.assertContains(response,"нет")
    def test_model_and_action_not_replaced(self):
        import inspect
        from . import admin as lab_admin
        self.assertIn("ensure_facts_confirmed",inspect.getsource(Preparation.complete))
        self.assertNotIn("preparation_readiness",inspect.getsource(Preparation.complete))
        self.assertIn("preparation.complete()",inspect.getsource(lab_admin.complete_preparation))
