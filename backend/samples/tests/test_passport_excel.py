from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, RequestFactory
from django.urls import reverse
from openpyxl import Workbook
from lab.models import Sample, MaterialPassport
from lab.passport_excel import (HEADERS, INLINE, BatchForm, SamplePassportForm, parse_excel,
    proposed, fingerprint, seal, unseal, assign_passport, validate_batch_records)


def file(rows, headers=HEADERS):
    book = Workbook()
    sheet = book.active
    sheet.append(headers)
    for row in rows:
        sheet.append(row)
    result = BytesIO()
    book.save(result)
    return SimpleUploadedFile("passports.xlsx", result.getvalue())


def sample(values=("", "", ""), note=""):
    passport = SimpleNamespace(**dict(zip(("vir_catalogue_number", "species", "origin"), values)))
    return SimpleNamespace(pk=1, series="AB", number="1", code="AB No.1", material_passport_id=3 if any(values) else None,
                           material_passport=passport if any(values) else None, note=note)


class PassportExcelTests(SimpleTestCase):
    def test_headers(self):
        self.assertEqual(HEADERS, ("Серия", "Номер", "Кат № ВИР", "Вид", "Происхождение", "Примечание"))

    def test_parse_text_and_integer_number(self):
        rows = parse_excel(file([["AB", 1, "K-01", "Вид", "Источник", "Заметка"]]))
        self.assertEqual((rows[0]["series"], rows[0]["number"]), ("AB", "1"))
        self.assertEqual(rows[0]["note"], "Заметка")

    def test_preserve_extra_number_symbols(self):
        self.assertEqual(parse_excel(file([["AB", "5*", "", "", "", ""]]))[0]["number"], "5*")

    def test_duplicates_rejected(self):
        with self.assertRaises(ValidationError):
            parse_excel(file([["AB", "1"], ["AB", "1"]]))

    def test_different_series_same_number_allowed(self):
        self.assertEqual(len(parse_excel(file([["AB", "1"], ["CD", "1"]]))), 2)

    def test_missing_series_rejected(self):
        with self.assertRaises(ValidationError):
            parse_excel(file([["", "1"]]))

    def test_formula_rejected(self):
        with self.assertRaises(ValidationError):
            parse_excel(file([["AB", "1", "=1+1"]]))

    def test_wrong_headers_rejected(self):
        with self.assertRaises(ValidationError):
            parse_excel(file([["AB", "1"]], headers=("Номер", "Серия")))

    def test_fill_only_empty(self):
        s = sample(("Old", "", "Old origin"), "Old note")
        row = {"values": ["New", "Species", "New origin"], "note": "New note"}
        self.assertEqual(proposed(s, row, False), (("Old", "Species", "Old origin"), "Old note"))

    def test_overwrite_nonempty_only(self):
        s = sample(("Old", "Species", "Origin"), "Old note")
        row = {"values": ["New", "", ""], "note": ""}
        self.assertEqual(proposed(s, row, True), (("New", "Species", "Origin"), "Old note"))

    def test_fingerprint_detects_edit(self):
        s = sample(note="Before")
        before = fingerprint(s)
        s.note = "After"
        self.assertNotEqual(before, fingerprint(s))

    def test_signed_payload_bound_to_user_and_mode(self):
        user = SimpleNamespace(pk=1)
        token = seal({"mode": "existing", "records": []}, user)
        self.assertEqual(unseal(token, user, "existing")["records"], [])
        with self.assertRaises(ValidationError):
            unseal(token, SimpleNamespace(pk=2), "existing")
        with self.assertRaises(ValidationError):
            unseal(token, user, "batch")

    def test_tampered_payload_rejected(self):
        with self.assertRaises(ValidationError):
            unseal("invalid", SimpleNamespace(pk=1), "existing")

    def test_batch_requires_exact_pairs(self):
        data = {"series": "AB", "numbers": ["1", "2"]}
        validate_batch_records([{"series": "AB", "number": "2"}, {"series": "AB", "number": "1"}], data)
        with self.assertRaises(ValidationError):
            validate_batch_records([{"series": "CD", "number": "1"}], data)

    def test_copy_on_write_never_edits_original_passport(self):
        s = sample(("Old", "Species", "Origin"))
        old = s.material_passport
        with patch.object(MaterialPassport, "full_clean"), patch.object(MaterialPassport, "save"), patch("lab.passport_excel.audit"):
            assign_passport(s, ("New", "Species", "Origin"), SimpleNamespace(pk=1))
        self.assertIsNot(s.material_passport, old)
        self.assertEqual(old.vir_catalogue_number, "Old")
        self.assertEqual(s.material_passport.vir_catalogue_number, "New")

    def test_clear_inline_unlinks_without_deleting(self):
        s = sample(("Old", "", ""))
        self.assertTrue(assign_passport(s, ("", "", ""), SimpleNamespace(pk=1)))
        self.assertIsNone(s.material_passport)

    def test_card_has_three_text_fields(self):
        request = RequestFactory().get("/")
        request.user = get_user_model()(is_active=True, is_staff=True, is_superuser=True)
        ma = admin.site._registry[Sample]
        form = ma.get_form(request)()
        self.assertNotIn("material_passport", form.fields)
        self.assertEqual(tuple(ma.get_fieldsets(request)[0][1]["fields"][:3]), INLINE)
        for name in INLINE:
            self.assertIn(name, form.fields)

    def test_manual_batch_has_no_passport_selector(self):
        form = BatchForm()
        self.assertNotIn("material_passport", form.fields)
        self.assertIn("excel_file", form.fields)
        self.assertFalse(form.fields["excel_file"].required)

    def test_legacy_passport_edit_disabled(self):
        request = RequestFactory().get("/")
        pa = admin.site._registry[MaterialPassport]
        self.assertFalse(pa.has_add_permission(request))
        self.assertFalse(pa.has_change_permission(request))

    def test_routes(self):
        self.assertTrue(reverse("admin:lab_sample_passport_excel").endswith("/passport-excel/"))
        self.assertTrue(reverse("admin:lab_sample_passport_template").endswith("/passport-excel-template/"))
