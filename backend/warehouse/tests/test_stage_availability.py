from datetime import timedelta
from decimal import Decimal
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from django.test import TestCase, RequestFactory
from django.urls import reverse
from django.utils import timezone
from lab.models import Lot, Movement, Task, StockAvailability, WorkExpenseRequest
from lab.test_stage6a import Fixtures
from lab.stock_availability import build_rows
from lab.worklog6 import post_expense

class AvailabilityTests(Fixtures, TestCase):
    def row(self):
        return build_rows(Lot.objects.filter(pk=self.lot.pk))[0]
    def test_free_quantity(self):
        row = self.row()
        self.assertEqual(row["available"], Decimal("10"))
        self.assertEqual(row["pending_free"], 0)
    def test_pending_not_deducted_twice(self):
        self.line(qty="3")
        row = self.row()
        self.assertEqual(row["available"], Decimal("10"))
        self.assertEqual(row["pending_free"], Decimal("3"))
        self.assertEqual(row["free_shortage"], 0)
    def test_posted_no_longer_pending(self):
        self.line(qty="3")
        post_expense(self.work.pk, self.leader)
        row = self.row()
        self.assertEqual(row["available"], Decimal("7"))
        self.assertEqual(row["pending_free"], 0)
    def test_free_shortage(self):
        self.line(qty="12")
        self.assertEqual(self.row()["free_shortage"], Decimal("2"))
    def test_expired_is_physically_present_but_unusable(self):
        self.lot.expiry = timezone.localdate() - timedelta(days=1)
        self.lot.save()
        row = self.row()
        self.assertEqual(row["lot"].quantity, Decimal("10"))
        self.assertEqual(row["available"], 0)
    def test_unreleased_is_unusable(self):
        self.lot.released = False
        self.lot.save()
        self.assertEqual(self.row()["available"], 0)
    def test_own_reserve_not_free_stock(self):
        Movement.objects.create(lot=self.lot, actor=self.leader, kind="reserve", qty=Decimal("4"), task=self.task, note="Reserve")
        self.line(qty="3", mode="use_reserved")
        row = self.row()
        self.assertEqual(row["available"], Decimal("6"))
        self.assertEqual(row["reserve_shortage"], 0)
        self.assertEqual(row["pending_free"], 0)
    def test_other_task_reserve_does_not_cover(self):
        other = Task.objects.create(title="Other", project=self.project, owner=self.executor, reviewer=self.leader, due=timezone.localdate())
        Movement.objects.create(lot=self.lot, actor=self.leader, kind="reserve", qty=Decimal("4"), task=other, note="Reserve")
        self.line(qty="3", mode="use_reserved")
        self.assertEqual(self.row()["reserve_shortage"], Decimal("3"))
    def test_page_read_only_and_no_data_change(self):
        self.line()
        before = (self.balance(), Movement.objects.count(), WorkExpenseRequest.objects.count())
        self.client.force_login(self.executor)
        url = reverse("admin:lab_stockavailability_changelist")
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 405)
        self.assertEqual(before, (self.balance(), Movement.objects.count(), WorkExpenseRequest.objects.count()))
    def test_admin_write_permissions_disabled(self):
        request = RequestFactory().get("/")
        request.user = self.leader
        ma = admin.site._registry[StockAvailability]
        self.assertFalse(ma.has_add_permission(request))
        self.assertFalse(ma.has_change_permission(request))
        self.assertFalse(ma.has_delete_permission(request))
