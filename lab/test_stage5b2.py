from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.admin.widgets import AutocompleteSelect
from django.core.exceptions import ValidationError
from django.db.models.deletion import PROTECT
from django.test import SimpleTestCase, RequestFactory
from .models import MaterialPassport, Sample
from .sample_dates import SAMPLE_FIELDS
from .sample_stage5b_admin import Stage5BBatchForm
from .sample_batch import can_edit_samples

class Stage5B2Tests(SimpleTestCase):
    def test_blank_passport_rejected(self):
        with self.assertRaises(ValidationError):
            MaterialPassport().clean()

    def test_whitespace_passport_rejected(self):
        with self.assertRaises(ValidationError):
            MaterialPassport(vir_catalogue_number="  ", species="\t", origin="\n").clean()

    def test_any_one_field_allowed(self):
        for field in ("vir_catalogue_number", "species", "origin"):
            obj = MaterialPassport(**{field: " Test "})
            obj.clean()
            self.assertEqual(getattr(obj, field), "Test")

    def test_catalogue_number_is_not_unique(self):
        self.assertFalse(MaterialPassport._meta.get_field("vir_catalogue_number").unique)

    def test_passport_optional_for_sample(self):
        field = Sample._meta.get_field("material_passport")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertIsNone(Sample().material_passport_id)
        self.assertIs(field.remote_field.on_delete, PROTECT)

    def test_batch_passport_optional(self):
        form = Stage5BBatchForm()
        self.assertFalse(form.fields["material_passport"].required)
        self.assertIsInstance(form.fields["material_passport"].widget, AutocompleteSelect)
        names = list(form.fields)
        self.assertLess(names.index("material_passport"), names.index("sample_type"))
        start = names.index("sample_type")
        self.assertEqual(tuple(names[start:start+5]), SAMPLE_FIELDS)

    def test_admin_registration_and_card(self):
        request = RequestFactory().get("/")
        request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        ma = admin.site._registry[Sample]
        groups = ma.get_fieldsets(request)
        self.assertEqual(tuple(groups[0][1]["fields"][:3]), ("passport_vir", "passport_species", "passport_origin"))
        form = ma.get_form(request)()
        self.assertNotIn("material_passport", form.fields)
        self.assertTrue(all(name in form.fields for name in ("passport_vir", "passport_species", "passport_origin")))
        self.assertTrue(admin.site.is_registered(MaterialPassport))

    def test_passport_delete_disabled(self):
        request = RequestFactory().get("/")
        self.assertFalse(admin.site._registry[MaterialPassport].has_delete_permission(request))

    def test_inactive_staff_cannot_edit(self):
        user = get_user_model()(is_active=False, is_staff=True, is_superuser=True)
        self.assertFalse(can_edit_samples(user))

    def test_passport_label(self):
        self.assertIn("ВИР K-123", str(MaterialPassport(vir_catalogue_number="K-123")))
