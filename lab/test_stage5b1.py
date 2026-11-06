from datetime import date, timedelta
from unittest.mock import patch
from django.contrib import admin
from django.contrib.admin.widgets import AdminDateWidget
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, RequestFactory
from django.urls import reverse
from .models import Sample
from .sample_dates import SAMPLE_FIELDS, validate_sample_dates
from .sample_stage5b_admin import Stage5BBatchForm

class Stage5B1Tests(SimpleTestCase):
    def setUp(self):
        self.today = date(2026, 11, 5)
        self.clock = patch("lab.sample_dates.timezone.localdate", return_value=self.today)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_old_samples_default_to_unknown(self):
        sample = Sample()
        self.assertEqual(sample.sample_type, "")
        self.assertEqual(sample.plant_part, "")
        self.assertEqual(sample.sampling_location, "")
        self.assertIsNone(sample.sampling_date)
        self.assertIsNone(sample.received_in_lab_date)

    def test_no_automatic_date_default(self):
        for name in ("sampling_date", "received_in_lab_date"):
            field = Sample._meta.get_field(name)
            self.assertTrue(field.null)
            self.assertTrue(field.blank)
            self.assertFalse(field.has_default())
            self.assertFalse(field.auto_now)
            self.assertFalse(field.auto_now_add)

    def test_choices(self):
        self.assertEqual(list(Sample._meta.get_field("sample_type").choices),
                         [("genotype", "генотип"), ("bulk", "bulk-проба")])

    def test_material_type_remains_separate(self):
        self.assertEqual(str(Sample._meta.get_field("kind").verbose_name), "Тип материала")
        self.assertEqual(str(Sample._meta.get_field("sample_type").verbose_name), "Тип пробы")

    def test_unknown_dates_allowed(self):
        validate_sample_dates(None, None)
        validate_sample_dates(self.today, None)
        validate_sample_dates(None, self.today)

    def test_same_day_allowed(self):
        validate_sample_dates(self.today, self.today)

    def test_correct_order_allowed(self):
        validate_sample_dates(self.today - timedelta(days=3), self.today)

    def test_future_sampling_rejected(self):
        with self.assertRaises(ValidationError) as result:
            validate_sample_dates(self.today + timedelta(days=1), None)
        self.assertIn("sampling_date", result.exception.message_dict)

    def test_future_receipt_rejected(self):
        with self.assertRaises(ValidationError) as result:
            validate_sample_dates(None, self.today + timedelta(days=1))
        self.assertIn("received_in_lab_date", result.exception.message_dict)

    def test_reversed_dates_rejected(self):
        with self.assertRaises(ValidationError) as result:
            validate_sample_dates(self.today, self.today - timedelta(days=1))
        self.assertIn("received_in_lab_date", result.exception.message_dict)

    def test_model_clean_includes_date_validation(self):
        sample = Sample(sampling_date=self.today + timedelta(days=1))
        with self.assertRaises(ValidationError):
            sample.clean()

    def test_database_constraints_declared(self):
        names = {item.name for item in Sample._meta.constraints}
        self.assertTrue({"unique_sample_series_number", "sample_type_allowed_5b1",
                         "sample_received_not_before_sampling_5b1"}.issubset(names))

    def test_batch_field_order_and_active_storage(self):
        form = Stage5BBatchForm()
        names = list(form.fields)
        start = names.index("sample_type")
        self.assertEqual(tuple(names[start:start + 5]), SAMPLE_FIELDS)
        self.assertNotIn("location", form.fields)
        self.assertTrue(form.fields["storage_place"].required)
        self.assertIn("active", str(form.fields["storage_place"].queryset.query))

    def test_batch_widgets_have_calendar_media(self):
        form = Stage5BBatchForm()
        for name in ("sampling_date", "received_in_lab_date"):
            self.assertIsInstance(form.fields[name].widget, AdminDateWidget)
            self.assertIsNone(form.fields[name].initial)
        from django.templatetags.static import static
        self.assertIn(static("admin/js/admin/DateTimeShortcuts.js"), str(form.media))
        from django.templatetags.static import static
        self.assertIn(static("admin/js/calendar.js"), str(form.media))

    def test_card_field_order_and_widgets(self):
        request = RequestFactory().get("/admin/lab/sample/add/")
        request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        modeladmin = admin.site._registry[Sample]
        groups = modeladmin.get_fieldsets(request)
        self.assertTrue(any(tuple(options.get("fields", ())) == SAMPLE_FIELDS for title, options in groups))
        form = modeladmin.get_form(request)()
        for name in ("sampling_date", "received_in_lab_date"):
            self.assertIsInstance(form.fields[name].widget, AdminDateWidget)

    def test_batch_url_is_preserved(self):
        self.assertTrue(reverse("admin:lab_sample_batch_add").endswith("/lab/sample/batch-add/"))
