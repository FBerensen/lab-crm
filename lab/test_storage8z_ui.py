from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse

from .models import Audit, Movement
from .test_storage8z import Storage8ZServiceTests


class Storage8ZPageTests(TestCase):
    def setUp(self):
        Storage8ZServiceTests.setUp(self)
        self.client.force_login(self.user)
        self.url = reverse(
            "admin:lab_storageplace_place8z", args=[self.target.pk]
        )
        self.contents_url = reverse(
            "admin:lab_storageplace_contents", args=[self.target.pk]
        )

    def preview(self, group="samples", obj=None, client=None):
        client = client or self.client
        obj = obj or self.sample
        response = client.get(self.url, {
            "group": group,
            "object": obj.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["preview"])
        return response, response.context["form"].initial["token"]

    def test_button_and_selection_are_readonly(self):
        audits = Audit.objects.count()
        response = self.client.get(self.contents_url)
        self.assertContains(response, self.url)
        self.assertContains(response, "Разместить материал")

        response = self.client.get(self.url, {
            "group": "samples", "q": self.sample.series,
        })
        self.assertContains(response, self.sample.code)
        self.preview()

        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)
        self.assertEqual(Audit.objects.count(), audits)

    def test_reader_has_no_button_and_cannot_open_form(self):
        reader = get_user_model().objects.create_user(
            username="storage8z_ui_reader", is_staff=True
        )
        self.client.force_login(reader)
        response = self.client.get(self.contents_url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.url)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_sample_confirmation_moves_and_audits(self):
        response, token = self.preview()
        self.assertTrue(response.context["is_transfer"])
        self.assertContains(response, self.source.name)
        self.assertContains(response, self.target.name)

        audits = Audit.objects.count()
        original = self.sample.original_location
        response = self.client.post(self.url, {
            "token": token, "confirm": "on",
        })
        self.assertRedirects(
            response, self.contents_url, fetch_redirect_response=False
        )
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.target.pk)
        self.assertEqual(self.sample.location, self.target.name)
        self.assertEqual(self.sample.original_location, original)
        self.assertEqual(Audit.objects.count(), audits + 1)

    def test_lot_confirmation_preserves_stock(self):
        fields = (
            "quantity", "reserved", "unit", "released", "code",
            "initial_quantity", "category_id",
        )
        before = {key: getattr(self.lot, key) for key in fields}
        movements = Movement.objects.count()
        response, token = self.preview("lots", self.lot)
        self.assertTrue(response.context["is_lot"])

        response = self.client.post(self.url, {
            "token": token, "confirm": "on",
        })
        self.assertEqual(response.status_code, 302)
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.storage_place_id, self.target.pk)
        self.assertEqual(
            before, {key: getattr(self.lot, key) for key in fields}
        )
        self.assertEqual(Movement.objects.count(), movements)

    def test_tampered_token_rejected(self):
        response, token = self.preview()
        audits = Audit.objects.count()
        response = self.client.post(self.url, {
            "token": token + "broken", "confirm": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)
        self.assertEqual(Audit.objects.count(), audits)

    def test_changed_source_rejected(self):
        response, token = self.preview()
        self.sample.storage_place = self.target
        self.sample.save(update_fields=["storage_place"])

        audits = Audit.objects.count()
        response = self.client.post(self.url, {
            "token": token, "confirm": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(Audit.objects.count(), audits)

    def test_repeated_submission_does_not_duplicate_audit(self):
        response, token = self.preview()
        payload = {"token": token, "confirm": "on"}
        self.assertEqual(self.client.post(self.url, payload).status_code, 302)
        audits = Audit.objects.count()

        response = self.client.post(self.url, payload)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(Audit.objects.count(), audits)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.target.pk)

    def test_confirmation_checkbox_required(self):
        response, token = self.preview()
        response = self.client.post(self.url, {"token": token})
        self.assertEqual(response.status_code, 200)
        self.assertIn("confirm", response.context["form"].errors)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)

    def test_token_is_bound_to_destination(self):
        response, token = self.preview()
        other_url = reverse(
            "admin:lab_storageplace_place8z", args=[self.source.pk]
        )
        audits = Audit.objects.count()
        response = self.client.post(other_url, {
            "token": token, "confirm": "on",
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)
        self.assertEqual(Audit.objects.count(), audits)

    def test_post_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        response, token = self.preview(client=client)
        response = client.post(self.url, {
            "token": token, "confirm": "on",
        })
        self.assertEqual(response.status_code, 403)
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)