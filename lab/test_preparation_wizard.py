from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from .models import PreparationProtocol, PreparationProtocolComponent, BufferName, ProductCategory, StoragePlace, Lot, Preparation, Movement, Component
from .preparation_wizard import WizardForm, new_token


class PreparationWizardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="wizard_employee", is_staff=True)
        group, _ = Group.objects.get_or_create(name="Исполнители")
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.category = ProductCategory.objects.create(name="wizard category")
        self.place = StoragePlace.objects.create(name="wizard shelf")
        self.product = BufferName.objects.create(name="wizard buffer")
        self.protocol = PreparationProtocol.objects.create(product=self.product, title="wizard protocol", base_volume_ml=100, instructions="Test only")
        self.line = PreparationProtocolComponent.objects.create(protocol=self.protocol, reagent_name="source", quantity=100, unit="ml")
        self.source = Lot.objects.create(name="wizard source", unit="мл", quantity=1000, reserved=0, released=True, category=self.category, storage_place=self.place)
        self.line.allowed_lots.add(self.source)
        self.url = reverse("admin:lab_preparation_wizard")

    def data(self):
        return {"recipe": str(self.protocol.pk), "planned_volume_ml": "100", "actual_day": timezone.localdate().isoformat(),
            "actual_volume_ml": "95", "output_category": str(self.category.pk), "output_storage_place": str(self.place.pk),
            f"lot_{self.line.pk}": str(self.source.pk), f"qty_{self.line.pk}": "100", "note": "wizard test"}

    def ready(self):
        data = self.data()
        response = self.client.post(self.url, {**data, "action": "preview"})
        self.assertEqual(response.status_code, 200)
        data["request_token"] = response.context["form"]["request_token"].value()
        self.assertTrue(data["request_token"])
        data["action"] = "save"
        return data

    def test_get_renders_without_writes(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Приготовление по протоколу")
        self.assertEqual(Preparation.objects.count(), 0)
        self.assertEqual(Movement.objects.count(), 0)

    def test_all_presets(self):
        for volume in (50, 100, 200, 250, 500, 800, 1000):
            with self.subTest(volume=volume):
                response = self.client.post(self.url, {**self.data(), "preset": str(volume)})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["form"].cleaned_data["planned_volume_ml"], Decimal(volume))
                self.assertEqual(self.line.required_quantity(Decimal(volume)), Decimal(volume))
        self.assertFalse(Preparation.objects.exists())

    def test_custom_volume(self):
        response = self.client.post(self.url, {**self.data(), "planned_volume_ml": "137.5", "action": "preview"})
        self.assertEqual(response.context["rows"][0]["plan"], "137.5 мл")

    def test_zero_volume_rejected(self):
        response = self.client.post(self.url, {**self.data(), "planned_volume_ml": "0", "action": "preview"})
        self.assertIn("planned_volume_ml", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_preview_does_not_post_stock(self):
        self.ready()
        self.source.refresh_from_db()
        self.assertEqual(self.source.quantity, Decimal("1000"))
        self.assertFalse(Movement.objects.exists())
        self.assertFalse(Preparation.objects.exists())

    def test_preview_never_fills_fact(self):
        data = self.data()
        data.pop(f"qty_{self.line.pk}")
        data.pop("actual_volume_ml")
        response = self.client.post(self.url, {**data, "action": "preview"})
        self.assertIsNone(response.context["form"][f"qty_{self.line.pk}"].value())
        self.assertIsNone(response.context["form"]["actual_volume_ml"].value())

    def test_save_creates_unprocessed_draft(self):
        response = self.client.post(self.url, self.ready())
        self.assertEqual(response.status_code, 302)
        p = Preparation.objects.get()
        self.assertFalse(p.processed)
        self.assertIsNone(p.output_id)
        self.assertEqual(p.actor_id, self.user.pk)
        self.assertEqual(p.actual_volume_ml, Decimal("95"))
        self.assertEqual(p.planned_volume_ml, Decimal("100"))
        self.assertEqual(Component.objects.filter(preparation=p).count(), 1)
        self.assertFalse(Movement.objects.exists())
        self.source.refresh_from_db()
        self.assertEqual(self.source.quantity, Decimal("1000"))

    def test_repeated_submit_does_not_duplicate(self):
        data = self.ready()
        first = self.client.post(self.url, data)
        second = self.client.post(self.url, data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(second.url, first.url)
        self.assertEqual(Preparation.objects.count(), 1)
        self.assertEqual(Component.objects.count(), 1)

    def test_volume_change_needs_recalculation(self):
        data = self.ready()
        data["planned_volume_ml"] = "200"
        response = self.client.post(self.url, data)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(Preparation.objects.exists())

    def test_recipe_change_needs_recalculation(self):
        data = self.ready()
        self.protocol.instructions = "changed"
        self.protocol.save(update_fields=["instructions"])
        response = self.client.post(self.url, data)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertFalse(Preparation.objects.exists())

    def test_forged_token_rejected(self):
        data = self.ready()
        data["request_token"] = "invalid"
        self.client.post(self.url, data)
        self.assertFalse(Preparation.objects.exists())

    def test_missing_actual_quantity_rejected(self):
        data = self.ready()
        del data[f"qty_{self.line.pk}"]
        response = self.client.post(self.url, data)
        self.assertIn(f"qty_{self.line.pk}", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_missing_actual_volume_rejected(self):
        data = self.ready()
        del data["actual_volume_ml"]
        response = self.client.post(self.url, data)
        self.assertIn("actual_volume_ml", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_non_allowed_lot_rejected(self):
        data = self.ready()
        other = Lot.objects.create(name="other", unit="мл", quantity=100, released=True)
        data[f"lot_{self.line.pk}"] = str(other.pk)
        response = self.client.post(self.url, data)
        self.assertIn(f"lot_{self.line.pk}", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_unreleased_lot_rejected(self):
        data = self.ready()
        Lot.objects.filter(pk=self.source.pk).update(released=False)
        response = self.client.post(self.url, data)
        self.assertIn(f"lot_{self.line.pk}", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_mass_volume_mismatch_rejected(self):
        data = self.ready()
        Lot.objects.filter(pk=self.source.pk).update(unit="г")
        response = self.client.post(self.url, data)
        self.assertIn(f"qty_{self.line.pk}", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_inactive_protocol_rejected(self):
        data = self.ready()
        PreparationProtocol.objects.filter(pk=self.protocol.pk).update(active=False)
        response = self.client.post(self.url, data)
        self.assertIn("recipe", response.context["form"].errors)
        self.assertFalse(Preparation.objects.exists())

    def test_to_final_volume_not_invented(self):
        self.line.to_final_volume = True
        self.line.quantity = None
        self.line.save()
        response = self.client.post(self.url, {**self.data(), "action": "preview"})
        self.assertIn("Довести до", response.context["rows"][0]["plan"])
        self.assertIn("по факту", response.context["rows"][0]["lot_plan"])

    def test_plan_unit_conversion(self):
        Lot.objects.filter(pk=self.source.pk).update(unit="л", quantity=10)
        response = self.client.post(self.url, {**self.data(), "action": "preview"})
        self.assertEqual(response.context["rows"][0]["lot_plan"], "0.1 л")

    def test_shortage_warning(self):
        Lot.objects.filter(pk=self.source.pk).update(quantity=50)
        response = self.client.post(self.url, {**self.data(), "action": "preview"})
        self.assertTrue(response.context["rows"][0]["warning"])

    def test_anonymous_redirected(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_nonstaff_denied(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_csrf_enforced(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, self.data()).status_code, 403)

    def test_actor_not_taken_from_post(self):
        data = self.ready()
        data["actor"] = "999999"
        self.client.post(self.url, data)
        self.assertEqual(Preparation.objects.get().actor_id, self.user.pk)

    def test_created_draft_uses_existing_complete_once(self):
        self.client.post(self.url, self.ready())
        p = Preparation.objects.get()
        p.complete()
        p.refresh_from_db()
        self.source.refresh_from_db()
        self.assertTrue(p.processed)
        self.assertEqual(self.source.quantity, Decimal("900"))
        self.assertEqual(p.output.quantity, Decimal("95"))
        self.assertFalse(p.output.released)
        self.assertEqual(Movement.objects.filter(preparation=p).count(), 2)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):
            p.complete()

    def test_link_on_list(self):
        response = self.client.get(reverse("admin:lab_preparation_changelist"))
        self.assertContains(response, self.url)
