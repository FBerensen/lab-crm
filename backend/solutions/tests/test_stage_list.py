from decimal import Decimal
from unittest.mock import patch
from django.contrib import admin
from django.db import connection
from django.test import TestCase,RequestFactory
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from lab.test_stage7v import Data
from lab.models import Preparation,Component,Lot,Movement,Audit
from lab.preparation_list7d import stage_code,PreparationStageFilter,PreparationOriginFilter,PreparationApprovalFilter

class PreparationListTests(Data,TestCase):
    def setUp(self):
        super().setUp();self.ma=admin.site._registry[Preparation]
        self.request=RequestFactory().get("/");self.request.user=self.executor
    def row(self,p):return self.ma.get_queryset(self.request).get(pk=p.pk)
    def ready(self):
        p=self.draft();self.fill(p);self.confirm(p);return p
    def test_existing_title_link_and_actions_preserved(self):
        self.assertEqual(self.ma.list_display_links,("preparation_title",))
        self.assertIn("preparation_title",self.ma.list_display)
        self.assertIn("complete_preparation",self.ma.get_actions(self.request))
    def test_draft_has_no_actual_output_in_list(self):
        p=self.draft();row=self.row(p)
        self.assertEqual(stage_code(row),"draft")
        self.assertIsNone(row.actual_volume_ml);self.assertIsNone(row.output_id)
        self.assertNotIn("qty",self.ma.list_display)
    def test_actual_values_mean_filling_not_ready(self):
        p=self.draft();self.fill(p)
        self.assertEqual(stage_code(self.row(p)),"facts")
    def test_confirmation_label_does_not_claim_current_readiness(self):
        p=self.ready();entry=p.components.get();entry.qty=Decimal("9");entry.save()
        row=self.row(p)
        self.assertEqual(stage_code(row),"confirmation")
        self.assertIn("проверить",self.ma.workflow_stage_7d(row))
    def test_posted_without_approval(self):
        p=self.ready();p.complete()
        self.assertEqual(stage_code(self.row(p)),"posted")
    def test_registered_approval_separate_from_stock_release(self):
        p=self.ready();p.complete();p.refresh_from_db()
        Preparation.objects.filter(pk=p.pk).update(approved_at=timezone.now(),approved_by=self.leader)
        Lot.objects.filter(pk=p.output_id).update(released=True)
        row=self.row(p);self.assertEqual(stage_code(row),"approved")
        Lot.objects.filter(pk=p.output_id).update(released=False)
        row=self.row(p);self.assertEqual(stage_code(row),"approved")
        self.assertIn("на складе: нет",self.ma.warehouse_state_7d(row))
    def test_inconsistent_marks_not_shown_as_approved(self):
        p=self.draft();Preparation.objects.filter(pk=p.pk).update(approved_at=timezone.now(),approved_by=self.leader)
        self.assertEqual(stage_code(self.row(p)),"review")
        posted=self.ready();posted.complete()
        Preparation.objects.filter(pk=posted.pk).update(approved_at=timezone.now(),approved_by=None)
        self.assertEqual(stage_code(self.row(posted)),"review")
        Preparation.objects.filter(pk=posted.pk).update(approved_at=None,approved_by=self.leader)
        self.assertEqual(stage_code(self.row(posted)),"review")
    def test_other_operations_explicit(self):
        p=self.draft();Preparation.objects.filter(pk=p.pk).update(kind="Автоклавирование расходников")
        self.assertEqual(stage_code(self.row(p)),"other")
    def test_stage_filter(self):
        p=self.draft()
        f=PreparationStageFilter(self.request,{"prep_stage":["draft"]},Preparation,self.ma)
        self.assertIn(p.pk,f.queryset(self.request,self.ma.get_queryset(self.request)).values_list("pk",flat=True))
        f=PreparationStageFilter(self.request,{"prep_stage":["posted"]},Preparation,self.ma)
        self.assertNotIn(p.pk,f.queryset(self.request,self.ma.get_queryset(self.request)).values_list("pk",flat=True))
    def test_origin_filter(self):
        p=self.draft()
        f=PreparationOriginFilter(self.request,{"prep_origin":["7v"]},Preparation,self.ma)
        self.assertIn(p.pk,f.queryset(self.request,self.ma.get_queryset(self.request)).values_list("pk",flat=True))
    def test_approval_filter(self):
        p=self.draft()
        f=PreparationApprovalFilter(self.request,{"prep_approval":["no"]},Preparation,self.ma)
        self.assertIn(p.pk,f.queryset(self.request,self.ma.get_queryset(self.request)).values_list("pk",flat=True))
    def test_statuses_use_one_annotated_query_not_readiness(self):
        a=self.draft();b=self.draft()
        with patch("lab.preparation_readiness.inspect_preparation",side_effect=AssertionError("full readiness called")),CaptureQueriesContext(connection) as captured:
            rows=list(self.ma.get_queryset(self.request).filter(pk__in=[a.pk,b.pk]))
            labels=[self.ma.workflow_stage_7d(row) for row in rows]
        self.assertEqual(len(labels),2);self.assertEqual(len(captured),1)
    def test_list_get_does_not_change_lab_data(self):
        p=self.draft();self.client.force_login(self.executor)
        before=(Preparation.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance())
        response=self.client.get(reverse("admin:lab_preparation_changelist"))
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse("admin:lab_preparation_readiness",args=[p.pk]))
        self.assertEqual(before,(Preparation.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance()))
    def test_change_rights_not_broadened(self):
        p=self.draft();request=RequestFactory().get("/");request.user=self.student
        self.assertFalse(self.ma.has_change_permission(request,p))
    def test_filters_and_dates_are_get_only(self):
        p=self.draft();self.client.force_login(self.executor)
        response=self.client.get(reverse("admin:lab_preparation_changelist"),{"prep_stage":"draft","prep_origin":"7v","prep_approval":"no"})
        self.assertEqual(response.status_code,200);self.assertContains(response,"Предпроверка 7Г")
        p.refresh_from_db();self.assertFalse(p.processed);self.assertIsNone(p.actual_volume_ml)
