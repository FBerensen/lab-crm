from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from lab.models import Audit, Lot, Movement, Project, Sample, StoragePlace
from lab.storage8z import place_material


class Storage8ZServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username="storage8z_admin",
            email="storage8z@example.invalid",
            password="test-only-password",
        )
        self.request = SimpleNamespace(user=self.user)
        self.project = Project.objects.create(
            title="Storage 8Z", kind="Проект", manager=self.user
        )
        self.source = StoragePlace.objects.create(name="8Z source")
        self.target = StoragePlace.objects.create(name="8Z target")
        self.sample = Sample.objects.create(
            series="Z8", number="1", project=self.project,
            kind="ДНК", storage_place=self.source,
            location="Историческое текстовое место",
        )
        self.lot = Lot.objects.create(
            name="8Z product", unit="мл", storage_place=self.source,
            quantity=Decimal("12.5"), reserved=Decimal("2"),
            initial_quantity=Decimal("12.5"), released=True,
        )

    def move(self, group="samples", obj=None, **kwargs):
        obj = obj or self.sample
        params = {"expected_place_id": self.source.pk}
        params.update(kwargs)
        return place_material(
            admin.site, self.request, group, obj.pk, self.target.pk,
            **params,
        )

    def test_sample_moves_and_preserves_original_text(self):
        original = self.sample.original_location
        obj, changed = self.move()
        obj.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(obj.storage_place_id, self.target.pk)
        self.assertEqual(obj.location, self.target.name)
        self.assertEqual(obj.original_location, original)
        self.assertEqual(obj.project_id, self.project.pk)
        self.assertEqual(obj.kind, "ДНК")

    def test_lot_stock_fields_are_unchanged(self):
        fields = (
            "quantity", "reserved", "unit", "released", "code",
            "initial_quantity", "category_id",
        )
        before = {field: getattr(self.lot, field) for field in fields}
        movements = Movement.objects.count()
        obj, changed = self.move("lots", self.lot)
        obj.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(obj.storage_place_id, self.target.pk)
        self.assertEqual(obj.location, self.target.name)
        self.assertEqual(before, {field: getattr(obj, field) for field in fields})
        self.assertEqual(Movement.objects.count(), movements)

    def test_move_writes_audit(self):
        before = Audit.objects.count()
        self.move()
        self.assertEqual(Audit.objects.count(), before + 1)
        entry = Audit.objects.get(
            entity=self.sample._meta.label,
            object_id=str(self.sample.pk),
        )
        self.assertEqual(entry.actor_id, self.user.pk)
        self.assertIn("Размещение материала", entry.detail)

    def test_stale_place_rejected(self):
        with self.assertRaises(ValidationError):
            self.move(expected_place_id=None)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)

    def test_inactive_destination_rejected(self):
        self.target.active = False
        self.target.save()
        with self.assertRaises(ValidationError):
            self.move()
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)

    def test_same_place_is_noop(self):
        before = Audit.objects.count()
        obj, changed = place_material(
            admin.site, self.request, "samples",
            self.sample.pk, self.source.pk,
            expected_place_id=self.source.pk,
        )
        self.assertFalse(changed)
        self.assertEqual(obj.storage_place_id, self.source.pk)
        self.assertEqual(Audit.objects.count(), before)

    def test_readonly_user_rejected(self):
        reader = get_user_model().objects.create_user(
            username="storage8z_reader", is_staff=True
        )
        self.request.user = reader
        with self.assertRaises(PermissionDenied):
            self.move()

    def test_object_permission_rechecked(self):
        modeladmin = admin.site._registry[Sample]
        with patch.object(modeladmin, "has_change_permission", return_value=False):
            with self.assertRaises(PermissionDenied):
                self.move()

    def test_hidden_object_rejected(self):
        modeladmin = admin.site._registry[Sample]
        with patch.object(
            modeladmin, "get_queryset", return_value=Sample.objects.none()
        ):
            with self.assertRaises(PermissionDenied):
                self.move()

    def test_unplaced_sample_can_be_placed(self):
        sample = Sample.objects.create(
            series="Z8", number="2", project=self.project,
            kind="ДНК", location="Неизвестное старое место",
        )
        original = sample.original_location
        obj, changed = self.move(
            obj=sample, expected_place_id=None
        )
        obj.refresh_from_db()
        self.assertTrue(changed)
        self.assertEqual(obj.storage_place_id, self.target.pk)
        self.assertEqual(obj.original_location, original)

    def test_audit_failure_rolls_back_move(self):
        with patch.object(
            Audit.objects, "create", side_effect=RuntimeError("audit failed")
        ):
            with self.assertRaises(RuntimeError):
                self.move()
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)

    def test_unknown_group_rejected(self):
        with self.assertRaises(ValidationError):
            self.move(group="unknown")