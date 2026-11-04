from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.contenttypes.models import ContentType
from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import (Preparation, Lot, ProductCategory, StoragePlace, Audit, Movement,
    BufferName, PreparationProtocol, PreparationProtocolComponent, Component, Autoclaving, AutoclavingItem)
from .preparation_access import can_approve_preparation, role_state


class PreparationStage2Tests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.employee = User.objects.create_user(username="s2_employee", is_staff=True)
        self.trainee = User.objects.create_user(username="s2_trainee", is_staff=True)
        self.lead = User.objects.create_user(username="s2_lead", is_staff=True)
        self.other = User.objects.create_user(username="s2_other", is_staff=True)
        self.store = User.objects.create_user(username="s2_store", is_staff=True)
        for user, name in ((self.employee, "Исполнители"), (self.trainee, "Стажёры"), (self.lead, "Руководители"), (self.store, "Склад")):
            group, _ = Group.objects.get_or_create(name=name)
            user.groups.add(group)
        ct = ContentType.objects.get_for_model(Preparation)
        self.permission, _ = Permission.objects.get_or_create(content_type=ct, codename="approve_preparation", defaults={"name": "Может допускать приготовленные растворы и буферы"})
        self.employee.user_permissions.add(self.permission)
        self.category = ProductCategory.objects.create(name="stage2-test")
        self.place = StoragePlace.objects.create(name="stage2-test")
        self.product = BufferName.objects.create(name="stage2 buffer", requires_autoclaving=True)
        self.protocol = PreparationProtocol.objects.create(product=self.product, title="stage2 protocol", base_volume_ml=100, instructions="test")

    def prep(self, actor=None, required=True):
        lot = Lot.objects.create(name="stage2 buffer", category=self.category, storage_place=self.place,
            unit="мл", quantity=100, reserved=0, released=False)
        return Preparation.objects.create(actor=actor or self.employee, actual_day=timezone.localdate(),
            kind="Приготовление реактива", protocol="stage2", recipe=self.protocol,
            planned_volume_ml=100, actual_volume_ml=100, qty=100, processed=True,
            output=lot, output_category=self.category, output_storage_place=self.place,
            autoclaving_required=required)

    def assert_blocked(self, p, user):
        with self.assertRaises(ValidationError):
            p.approve(user)
        p.output.refresh_from_db()
        self.assertFalse(p.output.released)

    def cycle(self, p, qty=100, status=Autoclaving.Status.COMPLETED):
        cycle = Autoclaving.objects.create(actor=self.employee, actual_day=timezone.localdate(), equipment="test", protocol="test", temperature_c=121,
            holding_time_min=20, control_result="test", status=status)
        AutoclavingItem.objects.create(cycle=cycle, lot=p.output, quantity=qty, unit="мл")
        return cycle

    def test_employee_self_approval(self):
        p = self.prep(required=False)
        p.approve(self.employee)
        p.output.refresh_from_db()
        self.assertTrue(p.output.released)
        self.assertEqual(p.approved_by_id, self.employee.pk)
        self.assertIsNotNone(p.approved_at)

    def test_employee_without_permission(self):
        self.employee.user_permissions.clear()
        self.assert_blocked(self.prep(required=False), self.employee)

    def test_trainee_cannot_approve_even_with_permission(self):
        self.trainee.user_permissions.add(self.permission)
        self.assert_blocked(self.prep(actor=self.trainee, required=False), self.trainee)

    def test_lead_can_approve_trainee(self):
        p = self.prep(actor=self.trainee, required=False)
        p.approve(self.lead)
        self.assertEqual(p.approved_by_id, self.lead.pk)

    def test_employee_can_approve_trainee(self):
        p = self.prep(actor=self.trainee, required=False)
        p.approve(self.employee)
        self.assertEqual(p.approved_by_id, self.employee.pk)

    def test_lead_self_approval(self):
        p = self.prep(actor=self.lead, required=False)
        p.approve(self.lead)
        self.assertEqual(p.approved_by_id, self.lead.pk)

    def test_unknown_role_denied_even_with_permission(self):
        self.other.user_permissions.add(self.permission)
        self.assert_blocked(self.prep(required=False), self.other)

    def test_warehouse_alone_denied(self):
        self.store.user_permissions.add(self.permission)
        self.assertFalse(can_approve_preparation(self.store))

    def test_conflicting_roles_denied(self):
        self.employee.groups.add(Group.objects.get(name="Стажёры"))
        self.assertEqual(role_state(self.employee), "conflict")
        self.assert_blocked(self.prep(required=False), self.employee)

    def test_inactive_denied(self):
        self.lead.is_active = False
        self.assertFalse(can_approve_preparation(self.lead))

    def test_nonstaff_denied(self):
        self.lead.is_staff = False
        self.assertFalse(can_approve_preparation(self.lead))

    def test_superuser_preserved(self):
        user = get_user_model().objects.create_user(username="s2_admin", is_staff=True, is_superuser=True)
        self.assertTrue(can_approve_preparation(user))

    def test_required_processing_blocks(self):
        self.assert_blocked(self.prep(), self.employee)

    def test_mark_then_approve(self):
        p = self.prep()
        p.mark_autoclaved(self.employee, confirmed_full_batch=True)
        self.assertTrue(p.autoclaving_done)
        self.assertEqual(p.autoclaved_by_id, self.employee.pk)
        self.assertIsNotNone(p.autoclaved_at)
        p.output.refresh_from_db()
        self.assertFalse(p.output.released)
        p.approve(self.employee)
        p.output.refresh_from_db()
        self.assertTrue(p.output.released)
        self.assertIsNone(p.approved_cycle_id)
        self.assertEqual(Audit.objects.filter(object_id=str(p.pk), entity="lab.Preparation").count(), 2)

    def test_mark_requires_full_batch_confirmation(self):
        p = self.prep()
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.employee)
        p.refresh_from_db()
        self.assertFalse(p.autoclaving_done)

    def test_trainee_can_mark_own_processing_but_not_approve(self):
        p = self.prep(actor=self.trainee)
        p.mark_autoclaved(self.trainee, confirmed_full_batch=True)
        self.assert_blocked(p, self.trainee)
        p.approve(self.employee)

    def test_other_employee_cannot_mark(self):
        p = self.prep(actor=self.trainee)
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.employee, confirmed_full_batch=True)

    def test_lead_can_mark(self):
        p = self.prep(actor=self.trainee)
        p.mark_autoclaved(self.lead, confirmed_full_batch=True)
        self.assertEqual(p.autoclaved_by_id, self.lead.pk)

    def test_repeat_mark_cannot_overwrite(self):
        p = self.prep()
        p.mark_autoclaved(self.employee, confirmed_full_batch=True)
        timestamp = p.autoclaved_at
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.lead, confirmed_full_batch=True)
        p.refresh_from_db()
        self.assertEqual(p.autoclaved_at, timestamp)
        self.assertEqual(p.autoclaved_by_id, self.employee.pk)

    def test_repeat_approval_blocked(self):
        p = self.prep(required=False)
        p.approve(self.employee)
        self.assert_blocked_already_approved(p)

    def assert_blocked_already_approved(self, p):
        with self.assertRaises(ValidationError):
            p.approve(self.lead)
        self.assertEqual(p.approved_by_id, self.employee.pk)

    def test_historical_unknown_blocked(self):
        p = self.prep(required=None)
        self.assert_blocked(p, self.lead)
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.lead, confirmed_full_batch=True)

    def test_expired_blocked(self):
        p = self.prep(required=False)
        p.output.expiry = timezone.localdate() - timedelta(days=1)
        p.output.save(update_fields=["expiry"])
        self.assert_blocked(p, self.employee)

    def test_changed_quantity_blocks_mark(self):
        p = self.prep()
        Lot.objects.filter(pk=p.output_id).update(quantity=50)
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.employee, confirmed_full_batch=True)

    def test_reservation_blocks_mark(self):
        p = self.prep()
        Lot.objects.filter(pk=p.output_id).update(reserved=1)
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.employee, confirmed_full_batch=True)

    def test_unprocessed_cannot_mark(self):
        p = self.prep()
        Preparation.objects.filter(pk=p.pk).update(processed=False)
        with self.assertRaises(ValidationError):
            p.mark_autoclaved(self.employee, confirmed_full_batch=True)

    def test_no_requirement_cannot_mark(self):
        with self.assertRaises(ValidationError):
            self.prep(required=False).mark_autoclaved(self.employee, confirmed_full_batch=True)

    def test_legacy_full_cycle_still_supported(self):
        p = self.prep()
        cycle = self.cycle(p)
        p.approve(self.employee)
        self.assertEqual(p.approved_cycle_id, cycle.pk)
        self.assertFalse(p.autoclaving_done)

    def test_legacy_partial_cycle_blocked(self):
        p = self.prep()
        self.cycle(p, qty=50)
        self.assert_blocked(p, self.employee)

    def test_legacy_failed_cycle_blocked(self):
        p = self.prep()
        self.cycle(p, status=Autoclaving.Status.FAILED)
        self.assert_blocked(p, self.employee)

    def test_new_cycle_after_mark_requires_review(self):
        p = self.prep()
        p.mark_autoclaved(self.employee, confirmed_full_batch=True)
        self.cycle(p)
        self.assert_blocked(p, self.employee)

    def test_mark_and_approval_do_not_change_stock_or_movements(self):
        p = self.prep()
        count = Movement.objects.count()
        p.mark_autoclaved(self.employee, confirmed_full_batch=True)
        p.approve(self.employee)
        p.output.refresh_from_db()
        self.assertEqual(p.output.quantity, Decimal("100"))
        self.assertEqual(p.output.reserved, Decimal("0"))
        self.assertEqual(Movement.objects.count(), count)

    def test_database_rejects_incomplete_mark(self):
        p = self.prep()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Preparation.objects.filter(pk=p.pk).update(autoclaving_done=True)

    def test_admin_action_visibility(self):
        from django.test import RequestFactory
        modeladmin = admin.site._registry[Preparation]
        for user, visible in ((self.employee, True), (self.trainee, False), (self.lead, True), (self.other, False)):
            request = RequestFactory().get("/admin/lab/preparation/")
            request.user = user
            self.assertEqual("approve_preparations" in modeladmin.get_actions(request), visible)

    def test_mark_page_get_does_not_write(self):
        p = self.prep()
        self.client.force_login(self.employee)
        response = self.client.get(reverse("admin:lab_preparation_mark_autoclaved", args=[p.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "confirmed_full_batch")
        p.refresh_from_db()
        self.assertFalse(p.autoclaving_done)

    def test_mark_page_unchecked_does_not_write(self):
        p = self.prep()
        self.client.force_login(self.employee)
        response = self.client.post(reverse("admin:lab_preparation_mark_autoclaved", args=[p.pk]), {})
        self.assertEqual(response.status_code, 200)
        p.refresh_from_db()
        self.assertFalse(p.autoclaving_done)

    def test_mark_page_checked_records_author(self):
        p = self.prep()
        self.client.force_login(self.employee)
        response = self.client.post(reverse("admin:lab_preparation_mark_autoclaved", args=[p.pk]), {"confirmed_full_batch": "on"})
        self.assertEqual(response.status_code, 302)
        p.refresh_from_db()
        self.assertEqual(p.autoclaved_by_id, self.employee.pk)

    def test_unauthorized_mark_page(self):
        p = self.prep()
        self.client.force_login(self.other)
        response = self.client.post(reverse("admin:lab_preparation_mark_autoclaved", args=[p.pk]), {"confirmed_full_batch": "on"})
        self.assertEqual(response.status_code, 403)
        p.refresh_from_db()
        self.assertFalse(p.autoclaving_done)

    def test_admin_lists_render(self):
        self.prep()
        self.client.force_login(self.lead)
        for name in ("admin:index", "admin:lab_preparation_changelist", "admin:lab_lot_changelist"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200)

    def test_preparation_card_renders(self):
        p = self.prep()
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(reverse("admin:lab_preparation_change", args=[p.pk])).status_code, 200)

    def test_complete_still_posts_only_once(self):
        source = Lot.objects.create(name="source", category=self.category, storage_place=self.place, unit="г", quantity=10, reserved=0, released=True)
        line = PreparationProtocolComponent.objects.create(protocol=self.protocol, reagent_name="test", quantity=1, unit="g")
        line.allowed_lots.add(source)
        p = Preparation.objects.create(actor=self.employee, actual_day=timezone.localdate(), kind="Приготовление реактива", protocol="test", recipe=self.protocol,
            planned_volume_ml=100, actual_volume_ml=100, qty=100, output_category=self.category, output_storage_place=self.place)
        Component.objects.create(preparation=p, recipe_component=line, lot=source, qty=1)
        p.complete()
        p.refresh_from_db()
        source.refresh_from_db()
        self.assertEqual(source.quantity, Decimal("9"))
        self.assertEqual(Movement.objects.filter(preparation=p).count(), 2)
        self.assertFalse(p.output.released)
        with self.assertRaises(ValidationError):
            p.complete()
        self.assertEqual(Movement.objects.filter(preparation=p).count(), 2)
