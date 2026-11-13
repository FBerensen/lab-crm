import importlib
from decimal import Decimal
from unittest.mock import patch
from django.apps import apps
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase
from django.urls import reverse
from lab.models import StoragePlace, Sample, Project, Lot, Movement
from lab.storage_support import descendant_ids, place_path

TARGET = "61 комната, горизонтальный холодильник (-40С)"


class StorageStage4Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="s4_lead", is_staff=True)
        group, _ = Group.objects.get_or_create(name="Руководители")
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.project = Project.objects.create(title="storage project", kind="Проект", manager=self.user)
        self.root = StoragePlace.objects.create(name="s4 freezer")
        self.shelf = StoragePlace.objects.create(name="s4 shelf", parent=self.root)
        self.box = StoragePlace.objects.create(name="s4 box", parent=self.shelf)
        self.other = StoragePlace.objects.create(name="s4 unrelated")
        self.url = reverse("admin:lab_storageplace_contents", args=[self.root.pk])

    def sample(self, number="1", place=None, location=""):
        return Sample.objects.create(series="S4", number=number, project=self.project,
            kind="ДНК", storage_place=place, location=location)

    def lot(self, name="s4 product", place=None, quantity=10, unit="мл"):
        return Lot.objects.create(name=name, storage_place=place or self.box, unit=unit,
            quantity=quantity, reserved=0, released=True)

    def test_descendants(self):
        places = list(StoragePlace.objects.all())
        self.assertEqual(descendant_ids(self.root.pk, places), {self.root.pk, self.shelf.pk, self.box.pk})

    def test_path(self):
        self.assertEqual(place_path(self.box.pk, list(StoragePlace.objects.all())), "s4 freezer / s4 shelf / s4 box")

    def test_direct_self_parent_rejected(self):
        self.root.parent = self.root
        with self.assertRaises(ValidationError):
            self.root.save()

    def test_indirect_cycle_rejected(self):
        self.root.parent = self.box
        with self.assertRaises(ValidationError):
            self.root.save()

    def test_database_self_parent_rejected(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            StoragePlace.objects.filter(pk=self.root.pk).update(parent_id=self.root.pk)

    def test_parent_deletion_protected(self):
        with self.assertRaises(ProtectedError):
            self.root.delete()

    def test_sample_place_deletion_protected(self):
        place = StoragePlace.objects.create(name="s4 sample only")
        self.sample(place=place)
        with self.assertRaises(ProtectedError):
            place.delete()

    def test_selected_place_mirrors_name(self):
        sample = self.sample(place=self.box)
        self.assertEqual(sample.location, self.box.name)
        self.assertEqual(sample.original_location, "")

    def test_exact_legacy_text_links_new_sample(self):
        sample = self.sample(location=self.box.name)
        self.assertEqual(sample.storage_place_id, self.box.pk)
        self.assertEqual(sample.original_location, self.box.name)

    def test_unknown_text_not_guessed(self):
        sample = self.sample(location="Different freezer -40")
        self.assertIsNone(sample.storage_place_id)
        self.assertEqual(sample.original_location, "Different freezer -40")

    def test_move_preserves_original_text(self):
        sample = self.sample(location=self.box.name)
        sample.storage_place = self.other
        sample.save(update_fields=["storage_place"])
        sample.refresh_from_db()
        self.assertEqual(sample.location, self.other.name)
        self.assertEqual(sample.original_location, self.box.name)

    def test_snapshot_not_overwritten_by_save(self):
        sample = self.sample(location=self.box.name)
        sample.original_location = "changed"
        sample.save()
        sample.refresh_from_db()
        self.assertEqual(sample.original_location, self.box.name)

    def test_rename_updates_mirrors_not_snapshot_or_stock(self):
        sample = self.sample(location=self.box.name)
        lot = self.lot()
        old_name = self.box.name
        self.box.name = "s4 renamed box"
        self.box.save()
        sample.refresh_from_db()
        lot.refresh_from_db()
        self.assertEqual(sample.location, self.box.name)
        self.assertEqual(sample.original_location, old_name)
        self.assertEqual(lot.location, self.box.name)
        self.assertEqual(lot.quantity, Decimal("10"))
        self.assertFalse(Movement.objects.exists())

    def test_contents_include_children(self):
        sample = self.sample(place=self.box)
        lot = self.lot()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, sample.code)
        self.assertContains(response, lot.code)
        self.assertEqual(response.context["sample_page"].paginator.count, 1)
        self.assertEqual(response.context["lot_page"].paginator.count, 1)

    def test_contents_direct_only(self):
        self.sample(place=self.box)
        self.lot()
        response = self.client.get(self.url, {"include_children": "0"})
        self.assertEqual(response.context["sample_page"].paginator.count, 0)
        self.assertEqual(response.context["lot_page"].paginator.count, 0)

    def test_unrelated_not_included(self):
        self.sample(place=self.other)
        self.lot(place=self.other)
        response = self.client.get(self.url)
        self.assertEqual(response.context["sample_page"].paginator.count, 0)
        self.assertEqual(response.context["lot_page"].paginator.count, 0)

    def test_zero_lot_toggle(self):
        self.lot(quantity=0)
        self.assertEqual(self.client.get(self.url).context["lot_page"].paginator.count, 0)
        self.assertEqual(self.client.get(self.url, {"show_empty": "1"}).context["lot_page"].paginator.count, 1)

    def test_search(self):
        self.lot(name="distinctive chemical")
        response = self.client.get(self.url, {"q": "distinctive"})
        self.assertEqual(response.context["lot_page"].paginator.count, 1)
        self.assertEqual(self.client.get(self.url, {"q": "absent"}).context["lot_page"].paginator.count, 0)

    def test_pagination_52_samples(self):
        for i in range(1, 53):
            self.sample(number=str(i), place=self.box)
        first = self.client.get(self.url)
        second = self.client.get(self.url, {"sample_page": "2"})
        self.assertEqual(first.context["sample_page"].paginator.count, 52)
        self.assertEqual(len(first.context["sample_rows"]), 50)
        self.assertEqual(len(second.context["sample_rows"]), 2)

    def test_view_does_not_write(self):
        sample = self.sample(place=self.box)
        lot = self.lot()
        before = (StoragePlace.objects.count(), Sample.objects.count(), Lot.objects.count(), Movement.objects.count())
        self.client.get(self.url)
        after = (StoragePlace.objects.count(), Sample.objects.count(), Lot.objects.count(), Movement.objects.count())
        self.assertEqual(before, after)
        lot.refresh_from_db()
        self.assertEqual(lot.quantity, Decimal("10"))

    def test_anonymous_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_nonstaff_denied(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_sample_permission_respected(self):
        sample = self.sample(place=self.box)
        with patch.object(admin.site._registry[Sample], "has_view_permission", return_value=False):
            response = self.client.get(self.url)
            self.assertFalse(response.context["can_samples"])
            self.assertEqual(response.context["sample_rows"], [])

    def test_catalog_counts_and_link(self):
        self.sample(place=self.root)
        response = self.client.get(reverse("admin:lab_storageplace_changelist"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.url)

    def test_lead_can_organize_places(self):
        from django.test import RequestFactory
        request = RequestFactory().get("/")
        request.user = self.user
        modeladmin = admin.site._registry[StoragePlace]
        self.assertTrue(modeladmin.has_add_permission(request))
        self.assertTrue(modeladmin.has_change_permission(request, self.root))

    def test_employee_cannot_organize_places(self):
        from django.test import RequestFactory
        user = get_user_model().objects.create_user(username="s4_employee", is_staff=True)
        group, _ = Group.objects.get_or_create(name="Исполнители")
        user.groups.add(group)
        request = RequestFactory().get("/")
        request.user = user
        self.assertFalse(admin.site._registry[StoragePlace].has_change_permission(request, self.root))

    def test_migration_data_exact_mapping_and_snapshot(self):
        target, _ = StoragePlace.objects.get_or_create(name=TARGET)
        first = self.sample(location="unmatched first")
        second = self.sample(number="2", location="unmatched second")
        Sample.objects.filter(pk=first.pk).update(location=TARGET, original_location="", storage_place=None)
        Sample.objects.filter(pk=second.pk).update(location=TARGET + " ", original_location="", storage_place=None)
        module = importlib.import_module("lab.migrations.0011_link_sample_storage_locations")
        from types import SimpleNamespace
        module.link_existing_locations(apps, SimpleNamespace(connection=connection))
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.storage_place_id, target.pk)
        self.assertEqual(first.original_location, TARGET)
        self.assertIsNone(second.storage_place_id)
        self.assertEqual(second.original_location, TARGET + " ")

    def test_missing_place_filter_opens(self):
        self.sample(location="unknown shelf")
        response = self.client.get(reverse("admin:lab_sample_changelist"), {"storage_place__isnull": "1"})
        self.assertEqual(response.status_code, 200)

    def test_sample_card_renders(self):
        sample = self.sample(place=self.box)
        response = self.client.get(reverse("admin:lab_sample_change", args=[sample.pk]))
        self.assertEqual(response.status_code, 200)
