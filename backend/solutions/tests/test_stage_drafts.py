from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from unittest.mock import patch
from django.contrib import admin
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import connections
from django.test import TestCase,TransactionTestCase,RequestFactory
from django.urls import reverse
from lab.test_stage6a import Fixtures
from lab.models import (BufferName,PreparationProtocol,PreparationProtocolComponent,ProductCategory,StoragePlace,
    Preparation,PreparationDraftSelection,PreparationDraftLine,Component,Lot,Movement,Audit)
from lab.preparation_drafts import issue_token,create_draft,confirm_facts,fact_snapshot,digest,ensure_facts_confirmed
from lab.preparation_access import can_approve_preparation

class Data(Fixtures):
    def setUp(self):
        super().setUp()
        self.product=BufferName.objects.create(name="7V test",kind="buffer",active=True)
        self.recipe=PreparationProtocol.objects.create(product=self.product,title="7V recipe",version="1",base_volume_ml=Decimal("100"),instructions="Approved local recipe")
        self.component=PreparationProtocolComponent.objects.create(protocol=self.recipe,reagent_name="First",quantity=Decimal("10"),unit="ml")
        self.lot.unit="мл";self.lot.save();self.component.allowed_lots.add(self.lot)
        self.category,_=ProductCategory.objects.get_or_create(name="7V output category",defaults={"active":True})
        self.place,_=StoragePlace.objects.get_or_create(name="7V output place",defaults={"active":True})
        self.metadata={"output_category":self.category.pk,"output_storage_place":self.place.pk,"note":"Draft test"}
    def token(self,user=None,qty="10"):
        return issue_token(self.recipe.pk,"100",[{"component_id":self.component.pk,"lot_id":self.lot.pk,"qty":qty}],user or self.executor)
    def draft(self,user=None,token=None):return create_draft(token or self.token(user),user or self.executor,self.metadata)[0]
    def fill(self,p,qty="10"):
        p.actual_volume_ml=Decimal("100");p.qty=Decimal("100");p.save()
        return Component.objects.create(preparation=p,lot=self.lot,recipe_component=self.component,qty=Decimal(qty))
    def confirm(self,p,user=None):
        p.refresh_from_db()
        return confirm_facts(p.pk,user or self.executor,confirmed=True,expected_digest=digest(fact_snapshot(p)))

class DraftTests(Data,TestCase):
    def test_create_only_plan_no_fact_output_or_movement(self):
        before=(self.balance(),Lot.objects.count(),Movement.objects.count())
        p=self.draft()
        self.assertIsNone(p.actual_volume_ml);self.assertIsNone(p.output_id)
        self.assertFalse(p.processed);self.assertFalse(p.components.exists())
        self.assertEqual(p.draft_selection.lines.count(),1)
        self.assertEqual(before,(self.balance(),Lot.objects.count(),Movement.objects.count()))
    def test_same_request_idempotent(self):
        token=self.token()
        p,first=create_draft(token,self.executor,self.metadata)
        same,second=create_draft(token,self.executor,self.metadata)
        self.assertTrue(first);self.assertFalse(second);self.assertEqual(p.pk,same.pk)
        self.assertEqual(Preparation.objects.count(),1)
    def test_same_key_different_metadata_rejected(self):
        token=self.token();create_draft(token,self.executor,self.metadata)
        with self.assertRaises(ValidationError):create_draft(token,self.executor,{**self.metadata,"note":"Changed"})
        self.assertEqual(Preparation.objects.count(),1)
    def test_token_bound_to_user(self):
        token=self.token()
        with self.assertRaises(PermissionDenied):self.draft(self.student,token)
    def test_bad_token_rejected(self):
        with self.assertRaises(ValidationError):self.draft(token="invalid")
    def test_recipe_change_rejected(self):
        token=self.token();self.recipe.instructions="Changed";self.recipe.save()
        with self.assertRaises(ValidationError):self.draft(token=token)
        self.assertFalse(Preparation.objects.exists())
    def test_stock_change_rechecked(self):
        token=self.token()
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="use_free",qty=Decimal("2"),note="Stock changed")
        with self.assertRaises(ValidationError):self.draft(token=token)
        self.assertFalse(Preparation.objects.exists())
    def test_unknown_solvent_remains_null(self):
        self.component.quantity=None;self.component.to_final_volume=True;self.component.save()
        p=self.draft(token=self.token(qty=None))
        self.assertIsNone(p.draft_selection.lines.get().planned_qty)
        self.assertFalse(p.components.exists())
    def test_line_failure_rolls_back_preparation_and_header(self):
        token=self.token()
        with patch.object(PreparationDraftLine,"save",side_effect=ValidationError("fail")):
            with self.assertRaises(ValidationError):self.draft(token=token)
        self.assertFalse(Preparation.objects.exists());self.assertFalse(PreparationDraftSelection.objects.exists())
    def test_cannot_complete_without_facts(self):
        p=self.draft()
        with self.assertRaises(ValidationError):p.complete()
        self.assertEqual(self.balance()[0],Decimal("10"));self.assertFalse(Movement.objects.exists())
    def test_filled_but_unconfirmed_cannot_complete(self):
        p=self.draft();self.fill(p)
        with self.assertRaises(ValidationError):p.complete()
        self.assertFalse(Movement.objects.exists())
    def test_confirm_requires_checkbox(self):
        p=self.draft();self.fill(p)
        with self.assertRaises(ValidationError):confirm_facts(p.pk,self.executor,confirmed=False)
    def test_confirm_needs_current_reviewed_digest(self):
        p=self.draft();entry=self.fill(p);value=digest(fact_snapshot(p))
        entry.qty=Decimal("9");entry.save()
        with self.assertRaises(ValidationError):confirm_facts(p.pk,self.executor,confirmed=True,expected_digest=value)
    def test_confirm_does_not_spend_or_approve(self):
        p=self.draft();self.fill(p);self.confirm(p)
        p.refresh_from_db()
        self.assertFalse(p.processed);self.assertIsNone(p.approved_at)
        self.assertEqual(self.balance()[0],Decimal("10"));self.assertFalse(Movement.objects.exists())
    def test_modified_facts_need_reconfirmation(self):
        p=self.draft();entry=self.fill(p);self.confirm(p)
        entry.qty=Decimal("9");entry.save()
        with self.assertRaises(ValidationError):p.complete()
        self.assertFalse(Movement.objects.exists())
    def test_confirmed_complete_uses_existing_stock_workflow(self):
        p=self.draft();self.fill(p);self.confirm(p);p.complete();p.refresh_from_db()
        self.assertTrue(p.processed);self.assertEqual(self.balance()[0],Decimal("0"))
        self.assertIsNotNone(p.output_id);self.assertEqual(p.output.quantity,Decimal("100"))
        self.assertFalse(p.output.released);self.assertIsNone(p.approved_at)
    def test_repeat_confirmation_no_extra_audit(self):
        p=self.draft();self.fill(p);self.confirm(p);count=Audit.objects.count()
        self.assertFalse(self.confirm(p));self.assertEqual(Audit.objects.count(),count)
    def test_trainee_can_save_own_plan_but_gets_no_approval_right(self):
        p=self.draft(self.student);self.fill(p);self.confirm(p,self.student)
        self.assertFalse(can_approve_preparation(self.student));p.refresh_from_db()
        self.assertIsNone(p.approved_at);self.assertFalse(p.processed)
    def test_other_executor_cannot_confirm(self):
        p=self.draft();self.fill(p)
        with self.assertRaises(PermissionDenied):self.confirm(p,self.student)
    def test_lot_units_frozen_for_saved_plan(self):
        self.draft();self.lot.unit="л"
        with self.assertRaises(ValidationError):self.lot.save()
    def test_draft_card_saves_without_actual_volume(self):
        p=self.draft();self.client.force_login(self.executor)
        response=self.client.post(reverse("admin:lab_preparation_change",args=[p.pk]),{
            "actual_day":p.actual_day.isoformat(),"actual_volume_ml":"", "output_category":self.category.pk,
            "output_storage_place":self.place.pk,"output_expiry":"","note":"Updated draft",
            "components-TOTAL_FORMS":"1","components-INITIAL_FORMS":"0","components-MIN_NUM_FORMS":"0","components-MAX_NUM_FORMS":"1000",
            "components-0-recipe_component":"","components-0-lot":"","components-0-qty":"","_save":"Save"})
        self.assertEqual(response.status_code,302)
        p.refresh_from_db();self.assertIsNone(p.actual_volume_ml);self.assertEqual(p.note,"Updated draft")
    def test_7b_preview_offers_draft_without_writing(self):
        self.client.force_login(self.executor)
        from lab.preparation_allocation import pair_names
        take,qty=pair_names(self.component.pk,self.lot.pk)
        response=self.client.post(reverse("admin:lab_preparationcalculator_allocation"),{"protocol":self.recipe.pk,"volume_ml":"100",take:"on",qty:"10"})
        self.assertEqual(response.status_code,200)
        self.assertContains(response,reverse("admin:lab_preparationcalculator_create_draft"))
        self.assertFalse(Preparation.objects.exists())
    def test_create_view_review_and_commit(self):
        token=self.token();self.client.force_login(self.executor)
        url=reverse("admin:lab_preparationcalculator_create_draft")
        review=self.client.post(url,{"token":token,"action":"review"})
        self.assertEqual(review.status_code,200);self.assertFalse(Preparation.objects.exists())
        response=self.client.post(url,{"token":token,"action":"create",**self.metadata})
        self.assertEqual(response.status_code,302);self.assertEqual(Preparation.objects.count(),1)
    def test_old_preparation_has_no_new_confirmation_gate(self):
        p=Preparation.objects.create(actor=self.executor,kind="Приготовление реактива",recipe=self.recipe,
            protocol=str(self.recipe),qty=Decimal("100"),planned_volume_ml=Decimal("100"),actual_volume_ml=Decimal("100"),
            output_category=self.category,output_storage_place=self.place,note="Legacy")
        ensure_facts_confirmed(p)
        self.assertFalse(PreparationDraftSelection.objects.exists())

class DraftConcurrencyTests(Data,TransactionTestCase):
    def test_two_create_requests_make_one_preparation(self):
        from django.contrib.auth import get_user_model
        token=self.token();uid=self.executor.pk;metadata=dict(self.metadata)
        def run(_):
            connections.close_all()
            try:
                p,created=create_draft(token,get_user_model().objects.get(pk=uid),metadata)
                return p.pk,created
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:values=list(pool.map(run,range(2)))
        self.assertEqual(len({pk for pk,_ in values}),1)
        self.assertEqual(sorted(created for _,created in values),[False,True])
        self.assertEqual(Preparation.objects.count(),1);self.assertFalse(Movement.objects.exists())
