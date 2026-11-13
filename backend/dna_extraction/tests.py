from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connections, connection
from django.test import TestCase, TransactionTestCase, RequestFactory
from django.utils import timezone
from django.contrib.auth import get_user_model
from lab.test_stage6a import Fixtures
from lab.models import (Task, TaskEvent, WorkLog, Lot, Movement, LaboratoryProtocol, LaboratoryProtocolRun,
    LaboratoryProtocolRunSample, TaskMaterialPlan)
from lab.lab6b2_services import start_run, close_run, plan_metrics, validate_journal_samples
from lab.worklog6 import post_expense

class Data(Fixtures):
    def setUp(self):
        super().setUp()
        TaskEvent.objects.create(task=self.task,actor=self.executor,action="accept")
        self.task.refresh_from_db()
        self.protocol=LaboratoryProtocol.objects.create(code="PCR_TEST",title="Test protocol",version="1",method_kind="pcr",instructions="Approved local SOP",created_by=self.leader)
        self.run=LaboratoryProtocolRun.objects.create(task=self.task,protocol=self.protocol,created_by=self.executor)
        LaboratoryProtocolRunSample.objects.create(run=self.run,sample=self.sample)
    def begin(self):start_run(self.run.pk,self.executor);self.run.refresh_from_db()
    def plan(self,qty="10"):
        return TaskMaterialPlan.objects.create(task=self.task,lot=self.lot,planned_qty=Decimal(qty),created_by=self.executor,updated_by=self.executor)
    def journal(self):
        return WorkLog.objects.create(actor=self.executor,task=self.task,laboratory_run=self.run,operation="Run work",detail="Result")
    def submit(self):TaskEvent.objects.create(task=self.task,actor=self.executor,action="submit",note="Results")
    def approve(self):return TaskEvent.objects.create(task=self.task,actor=self.leader,action="approve")

class Stage6B2Tests(Data,TestCase):
    def test_start_snapshots_version_without_spending(self):
        self.begin()
        self.assertEqual(self.run.status,"running")
        self.assertEqual(self.run.protocol_snapshot["version"],"1")
        self.assertEqual(self.balance()[0],Decimal("10"))
        self.assertFalse(Movement.objects.exists())
    def test_start_requires_task_in_work(self):
        Task.objects.filter(pk=self.task.pk).update(status="Назначено")
        with self.assertRaises(ValidationError):self.begin()
    def test_used_protocol_cannot_change(self):
        self.protocol.instructions="Changed"
        with self.assertRaises(ValidationError):self.protocol.save()
    def test_deactivation_allowed(self):
        self.protocol.active=False;self.protocol.save()
        with self.assertRaises(ValidationError):self.begin()
    def test_samples_freeze_on_start(self):
        row=self.run.sample_rows.get();self.begin()
        with self.assertRaises(ValidationError):row.delete()
        with self.assertRaises(ValidationError):row.save()
    def test_journal_task_must_match(self):
        self.begin()
        other=Task.objects.create(title="Other",project=self.project,owner=self.executor,reviewer=self.leader,due=timezone.localdate())
        with self.assertRaises(ValidationError):WorkLog.objects.create(actor=self.executor,task=other,laboratory_run=self.run,operation="Work",detail="Result")
    def test_journal_samples_must_be_in_run(self):
        from lab.models import Sample
        second=Sample.objects.create(series="QB",number="2",project=self.project,kind="ДНК")
        self.task.samples.add(second);self.begin();work=self.journal()
        with self.assertRaises(ValidationError):validate_journal_samples(work,{second.pk})
    def test_draft_run_cannot_receive_journal(self):
        with self.assertRaises(ValidationError):self.journal()
    def test_pending_blocks_run_close(self):
        self.begin();work=self.journal();self.line(work=work)
        with self.assertRaises(ValidationError):close_run(self.run.pk,self.executor,"Result")
        self.run.refresh_from_db();self.assertEqual(self.run.status,"running")
    def test_post_in_journal_then_close_no_double_spend(self):
        self.begin();work=self.journal();self.line(work=work)
        post_expense(work.pk,self.leader)
        self.assertTrue(close_run(self.run.pk,self.executor,"Result"))
        self.assertFalse(close_run(self.run.pk,self.executor,"Result"))
        self.assertFalse(post_expense(work.pk,self.leader))
        self.assertEqual(self.balance()[0],Decimal("8"))
        self.assertEqual(Movement.objects.filter(worklog=work).count(),1)
    def test_closed_run_cannot_receive_new_expenses(self):
        self.begin();work=self.journal();close_run(self.run.pk,self.executor,"Result")
        with self.assertRaises(ValidationError):self.line(work=work)
    def test_unclosed_run_blocks_task_approval(self):
        self.submit()
        with self.assertRaises(ValidationError):self.approve()
        self.task.refresh_from_db();self.assertEqual(self.task.status,"На проверке")
    def test_pending_unlinked_work_blocks_task_approval(self):
        self.begin();close_run(self.run.pk,self.executor,"Result");self.line()
        self.submit()
        with self.assertRaises(ValidationError):self.approve()
    def test_approval_keeps_reviewer_rule(self):
        self.begin();close_run(self.run.pk,self.executor,"Result");self.submit()
        with self.assertRaises(ValidationError):TaskEvent.objects.create(task=self.task,actor=self.executor,action="approve")
        self.approve();self.task.refresh_from_db();self.assertEqual(self.task.status,"Завершено")
    def test_transfer_blocked_for_open_run(self):
        with self.assertRaises(ValidationError):TaskEvent.objects.create(task=self.task,actor=self.leader,action="transfer",target=self.stock,note="Transfer")
    def test_closed_task_no_new_work_or_expense(self):
        self.begin();close_run(self.run.pk,self.executor,"Result");self.submit();self.approve()
        with self.assertRaises(ValidationError):WorkLog.objects.create(actor=self.executor,task=self.task,operation="New",detail="New")
        with self.assertRaises(ValidationError):self.line()
    def test_plan_does_not_spend_or_reserve(self):
        p=self.plan()
        self.assertEqual(self.balance(),(Decimal("10"),Decimal("0")))
        r=plan_metrics([p])[0]
        self.assertEqual(r["remaining"],Decimal("10"));self.assertEqual(r["spent"],0)
    def test_plan_subtracts_actual_once(self):
        p=self.plan();self.line(qty="3");post_expense(self.work.pk,self.leader)
        r=plan_metrics([p])[0]
        self.assertEqual(r["spent"],Decimal("3"));self.assertEqual(r["remaining"],Decimal("7"))
    def test_pending_not_added_to_remaining(self):
        p=self.plan();self.line(qty="3")
        r=plan_metrics([p])[0]
        self.assertEqual(r["remaining"],Decimal("10"));self.assertEqual(r["pending"],Decimal("3"))
    def test_plan_overrun_visible(self):
        p=self.plan("1");self.line(qty="3");post_expense(self.work.pk,self.leader)
        r=plan_metrics([p])[0];self.assertEqual(r["remaining"],0);self.assertEqual(r["overrun"],Decimal("2"))
    def test_plan_own_reserve_covers_remaining(self):
        p=self.plan();Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="reserve",qty=Decimal("4"),note="Reserve")
        r=plan_metrics([p])[0];self.assertEqual(r["covered"],Decimal("4"));self.assertEqual(r["uncovered"],Decimal("6"))
    def test_plan_duplicate_and_zero_rejected(self):
        p=self.plan()
        with self.assertRaises(ValidationError):self.plan()
        p.planned_qty=Decimal("0")
        with self.assertRaises(ValidationError):p.save()
    def test_unit_change_blocked_when_planned(self):
        self.plan();self.lot.unit="other-unit"
        with self.assertRaises(ValidationError):self.lot.save()
    def test_run_close_requires_nonempty_result(self):
        self.begin()
        with self.assertRaises(ValidationError):close_run(self.run.pk,self.executor," ")
    def test_permissions_on_run_action(self):
        with self.assertRaises(PermissionDenied):start_run(self.run.pk,self.stock)
    def test_readonly_plan_report(self):
        from django.urls import reverse
        self.plan();self.client.force_login(self.executor)
        self.assertEqual(self.client.get(reverse("admin:lab_material_plan_report")).status_code,200)
    def test_run_admin_creation(self):
        from django.urls import reverse
        self.client.force_login(self.executor)
        response=self.client.post(reverse("admin:lab_laboratoryprotocolrun_add"),{
            "task":str(self.task.pk),"protocol":str(self.protocol.pk),"note":"New",
            "sample_rows-TOTAL_FORMS":"1","sample_rows-INITIAL_FORMS":"0","sample_rows-MIN_NUM_FORMS":"0","sample_rows-MAX_NUM_FORMS":"1000",
            "sample_rows-0-sample":str(self.sample.pk),"sample_rows-0-note":"", "_save":"Save"})
        self.assertEqual(response.status_code,302)
        self.assertEqual(LaboratoryProtocolRun.objects.filter(task=self.task).count(),2)


    def test_metrics_reload_cached_lot_quantity(self):
        p=self.plan()
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="use_free",qty=Decimal("3"),note="Use after plan loading")
        self.assertEqual(p.lot.quantity,Decimal("10"))
        row=plan_metrics([p])[0]
        self.assertEqual(row["spent"],Decimal("3"))
        self.assertEqual(row["remaining"],Decimal("7"))
        self.assertEqual(p.lot.quantity,Decimal("7"))

    def test_metrics_reload_cached_release_flag(self):
        p=self.plan()
        Movement.objects.create(lot=self.lot,actor=self.leader,task=self.task,kind="reserve",qty=Decimal("4"),note="Reserve")
        p.lot.refresh_from_db()
        self.assertTrue(p.lot.released)
        Lot.objects.filter(pk=self.lot.pk).update(released=False)
        row=plan_metrics([p])[0]
        self.assertFalse(p.lot.released)
        self.assertEqual(row["covered"],Decimal("0"))
        self.assertEqual(row["uncovered"],Decimal("10"))


class Stage6B2ClosingRaceTests(Fixtures,TransactionTestCase):
    def test_approval_and_new_pending_expense_serialize(self):
        TaskEvent.objects.create(task=self.task,actor=self.executor,action="accept")
        TaskEvent.objects.create(task=self.task,actor=self.executor,action="submit",note="Result")
        task_id,work_id,leader_id,lot_id=self.task.pk,self.work.pk,self.leader.pk,self.lot.pk
        def run(action):
            connections.close_all()
            try:
                if action=="close":
                    TaskEvent.objects.create(task_id=task_id,actor_id=leader_id,action="approve")
                else:
                    from lab.models import WorkExpenseLine
                    WorkExpenseLine.objects.create(worklog_id=work_id,lot_id=lot_id,qty=Decimal("1"),mode="use_free")
                return "ok"
            except ValidationError:return "blocked"
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,("close","line")))
        self.assertEqual(sorted(results),["blocked","ok"])
        self.task.refresh_from_db()
        if self.task.status=="Завершено":self.assertFalse(self.work.expense_lines.exists())
        else:self.assertTrue(self.work.expense_lines.exists())
