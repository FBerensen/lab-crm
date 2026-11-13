from unittest.mock import patch

from django.contrib import admin
from django.test import TestCase
from django.urls import reverse

from lab.models import Audit, Lot, Movement, Sample, SourceBlock
from lab.source_blocks8v import save_block, set_well, set_position
from lab.test_storage8z import Storage8ZServiceTests


class Storage8ZBlockPageTests(TestCase):
    def setUp(self):
        Storage8ZServiceTests.setUp(self)
        self.client.force_login(self.user)
        self.block = SourceBlock(
            name="8Z source block",
            default_dna_ul=1,
            default_water_ul=2,
            storage_place=self.source,
        )
        save_block(self.block, self.user, 0)
        self.url = reverse(
            "admin:lab_storageplace_place8z", args=[self.target.pk]
        )

    def revision(self):
        self.block.refresh_from_db()
        return self.block.revision

    def preview(self):
        response = self.client.get(self.url, {
            "group": "blocks",
            "object": self.block.pk,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["is_block"])
        return response.context["form"].initial["token"]

    def post(self, token):
        return self.client.post(self.url, {
            "token": token,
            "confirm": "on",
        })

    def test_block_moves_with_revision_and_audit(self):
        revision = self.revision()
        token = self.preview()
        audits = Audit.objects.count()

        response = self.post(token)
        self.assertEqual(response.status_code, 302)
        self.block.refresh_from_db()
        self.assertEqual(self.block.storage_place_id, self.target.pk)
        self.assertEqual(self.block.revision, revision + 1)
        self.assertEqual(Audit.objects.count(), audits + 1)

        detail = Audit.objects.order_by("-pk").first().detail
        self.assertIn(self.source.name, str(detail))
        self.assertIn(self.target.name, str(detail))

    def test_stale_revision_rejected_without_overwriting_metadata(self):
        token = self.preview()
        self.block.refresh_from_db()
        self.block.note = "Changed after preview"
        save_block(self.block, self.user, self.block.revision)
        revision = self.revision()
        audits = Audit.objects.count()

        response = self.post(token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.block.refresh_from_db()
        self.assertEqual(self.block.storage_place_id, self.source.pk)
        self.assertEqual(self.block.note, "Changed after preview")
        self.assertEqual(self.block.revision, revision)
        self.assertEqual(Audit.objects.count(), audits)

    def test_wells_samples_and_stock_unchanged(self):
        set_well(
            self.block.pk, "A", 1, self.sample.pk,
            self.user, self.revision(),
        )
        set_position(
            self.block.pk, "A", 2, self.user, self.revision(),
            kind="water", dna_ul=0,
        )
        set_position(
            self.block.pk, "A", 3, self.user, self.revision(),
            kind="unregistered", label="8Z unknown",
        )

        wells = list(self.block.wells.order_by("pk").values())
        samples = list(Sample.objects.order_by("pk").values())
        lots = list(Lot.objects.order_by("pk").values())
        movements = Movement.objects.count()

        response = self.post(self.preview())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(
            list(self.block.wells.order_by("pk").values()), wells
        )
        self.assertEqual(
            list(Sample.objects.order_by("pk").values()), samples
        )
        self.assertEqual(
            list(Lot.objects.order_by("pk").values()), lots
        )
        self.assertEqual(Movement.objects.count(), movements)

    def test_destination_disabled_after_preview_rejected(self):
        token = self.preview()
        revision = self.revision()
        audits = Audit.objects.count()
        self.target.active = False
        self.target.save()

        response = self.post(token)
        self.assertEqual(response.status_code, 400)
        self.block.refresh_from_db()
        self.assertEqual(self.block.storage_place_id, self.source.pk)
        self.assertEqual(self.block.revision, revision)
        self.assertEqual(Audit.objects.count(), audits)

    def test_permission_revoked_after_preview_rejected(self):
        token = self.preview()
        revision = self.revision()
        audits = Audit.objects.count()
        modeladmin = admin.site._registry[SourceBlock]

        with patch.object(
            modeladmin, "has_change_permission", return_value=False
        ):
            response = self.post(token)

        self.assertEqual(response.status_code, 403)
        self.block.refresh_from_db()
        self.assertEqual(self.block.storage_place_id, self.source.pk)
        self.assertEqual(self.block.revision, revision)
        self.assertEqual(Audit.objects.count(), audits)

    def test_repeated_block_confirmation_rejected(self):
        token = self.preview()
        self.assertEqual(self.post(token).status_code, 302)
        revision = self.revision()
        audits = Audit.objects.count()

        response = self.post(token)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())
        self.assertEqual(self.revision(), revision)
        self.assertEqual(Audit.objects.count(), audits)

    def test_block_search_by_code(self):
        response = self.client.get(self.url, {
            "group": "blocks",
            "q": self.block.code,
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.block.name)
        self.assertEqual(len(response.context["rows"]), 1)

    def test_preview_does_not_change_block(self):
        before = list(
            SourceBlock.objects.filter(pk=self.block.pk).values()
        )
        audits = Audit.objects.count()
        self.preview()
        self.assertEqual(
            list(SourceBlock.objects.filter(pk=self.block.pk).values()),
            before,
        )
        self.assertEqual(Audit.objects.count(), audits)