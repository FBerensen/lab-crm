import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from lab.models import Audit, Sample, StoragePlace
from lab import test_storage8z as service_tests


class Storage8ZExtendedTests(TestCase):
    def setUp(self):
        service_tests.Storage8ZServiceTests.setUp(self)
        self.client.force_login(self.user)
        self.url = reverse(
            "admin:lab_storageplace_place8z", args=[self.target.pk]
        )

    def token(self):
        response = self.client.get(self.url, {
            "group": "samples",
            "object": self.sample.pk,
        })
        self.assertEqual(response.status_code, 200)
        return response.context["form"].initial["token"]

    def post(self, token):
        return self.client.post(self.url, {
            "token": token,
            "confirm": "on",
        })

    def assert_unchanged(self, audits):
        self.sample.refresh_from_db()
        self.assertEqual(self.sample.storage_place_id, self.source.pk)
        self.assertEqual(Audit.objects.count(), audits)

    def test_expired_confirmation_rejected(self):
        old_time = time.time() - 1000
        with patch("django.core.signing.time.time", return_value=old_time):
            token = self.token()

        audits = Audit.objects.count()
        response = self.post(token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assert_unchanged(audits)

    def test_confirmation_bound_to_user(self):
        token = self.token()
        other = get_user_model().objects.create_superuser(
            username="storage8z_other_admin",
            email="other@example.invalid",
            password="test-only-password",
        )
        self.client.force_login(other)
        audits = Audit.objects.count()

        response = self.post(token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assert_unchanged(audits)

    def test_sample_permission_revoked_after_preview(self):
        token = self.token()
        audits = Audit.objects.count()
        modeladmin = admin.site._registry[Sample]

        with patch.object(
            modeladmin, "has_change_permission", return_value=False
        ):
            response = self.post(token)

        self.assertEqual(response.status_code, 403)
        self.assert_unchanged(audits)

    def test_sample_destination_disabled_after_preview(self):
        token = self.token()
        self.target.active = False
        self.target.save()
        audits = Audit.objects.count()

        response = self.post(token)
        self.assertEqual(response.status_code, 400)
        self.assert_unchanged(audits)

    def test_source_place_access_required(self):
        modeladmin = admin.site._registry[StoragePlace]
        original = modeladmin.has_view_permission

        def permitted(request, obj=None):
            if obj is not None and obj.pk == self.source.pk:
                return False
            return original(request, obj)

        audits = Audit.objects.count()
        with patch.object(
            modeladmin, "has_view_permission", side_effect=permitted
        ):
            response = self.client.get(self.url, {
                "group": "samples",
                "q": self.sample.series,
            })
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context["rows"], [])

            response = self.client.get(self.url, {
                "group": "samples",
                "object": self.sample.pk,
            })
            self.assertEqual(response.status_code, 403)

        self.assert_unchanged(audits)

    def test_pagination_preserves_search_and_group(self):
        for number in range(2, 28):
            Sample.objects.create(
                series="Z8",
                number=str(number),
                project=self.project,
                kind="ДНК",
                storage_place=self.source,
            )

        response = self.client.get(self.url, {
            "group": "samples",
            "q": "Z8",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].paginator.count, 27)
        self.assertEqual(len(response.context["rows"]), 25)

        next_url = response.context["links"]["next"]
        params = parse_qs(urlparse(next_url).query)
        self.assertEqual(params["group"], ["samples"])
        self.assertEqual(params["q"], ["Z8"])
        self.assertEqual(params["page"], ["2"])

        response = self.client.get(next_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page"].number, 2)
        self.assertEqual(len(response.context["rows"]), 2)