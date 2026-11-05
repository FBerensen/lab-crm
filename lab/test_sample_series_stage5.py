from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from .models import Sample, Project, StoragePlace, Movement
from .sample_series_stage5 import series_summary


class SampleSeriesStage5Tests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="series_user", is_staff=True)
        group, _ = Group.objects.get_or_create(name="Руководители")
        self.user.groups.add(group)
        self.client.force_login(self.user)
        self.project = Project.objects.create(title="series project", kind="Проект", manager=self.user)
        self.place = StoragePlace.objects.create(name="series place")
        self.url = reverse("admin:lab_sample_changelist")
        self.series_url = reverse("admin:lab_sample_series")

    def sample(self, series="QT", number="1", project=None, placed=True, parent=None):
        return Sample.objects.create(series=series, number=number, project=project or self.project,
            kind="ДНК", storage_place=self.place if placed else None, location="" if placed else "unmapped", parent=parent)

    def test_default_page_is_series_index(self):
        self.sample()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/lab/sample/series_index.html")
        self.assertEqual(response.context["rows"][0]["series"], "QT")

    def test_52_samples_in_one_series(self):
        for number in range(1, 53):
            self.sample(number=str(number))
        response = self.client.get(self.url)
        row = response.context["rows"][0]
        self.assertEqual(row["samples"], 52)
        self.assertEqual(row["projects"], 1)
        self.assertEqual(row["places"], 1)
        self.assertEqual(response.context["total_series"], 1)
        self.assertEqual(len(response.context["rows"]), 1)

    def test_multiple_series_counted_separately(self):
        self.sample()
        self.sample(series="AB")
        response = self.client.get(self.url)
        self.assertEqual(response.context["total_series"], 2)
        self.assertEqual(response.context["total_samples"], 2)

    def test_same_series_multiple_projects(self):
        second = Project.objects.create(title="second project", kind="Проект", manager=self.user)
        self.sample()
        self.sample(number="2", project=second)
        row = self.client.get(self.url).context["rows"][0]
        self.assertEqual(row["projects"], 2)
        self.assertEqual(len(row["project_names"]), 2)

    def test_missing_place_count(self):
        self.sample(placed=False)
        row = self.client.get(self.url).context["rows"][0]
        self.assertEqual(row["unplaced"], 1)
        self.assertEqual(row["places"], 0)

    def test_series_link_filters_exactly(self):
        wanted = self.sample()
        self.sample(series="QA")
        response = self.client.get(self.url)
        link = response.context["rows"][0]["url"]
        selected = self.client.get(link)
        self.assertEqual(selected.status_code, 200)
        self.assertTemplateUsed(selected, "admin/lab/sample/change_list.html")
        # Rows are alphabetic: follow the QT row explicitly.
        qt_link = next(r["url"] for r in response.context["rows"] if r["series"] == "QT")
        selected = self.client.get(qt_link)
        self.assertEqual(list(selected.context["cl"].queryset.values_list("pk", flat=True)), [wanted.pk])

    def test_all_samples_mode_preserves_original_template(self):
        self.sample()
        response = self.client.get(self.url, {"all_samples": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/lab/sample/change_list.html")
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_navigation_marker_is_not_orm_filter(self):
        self.sample()
        response = self.client.get(self.url, {"all_samples": "1", "o": "1"})
        self.assertEqual(response.status_code, 200)
        query = response.context["cl"].get_query_string()
        self.assertEqual(parse_qs(urlsplit(query).query).get("all_samples"), ["1"])

    def test_global_search_remains_flat(self):
        wanted = self.sample()
        self.sample(series="AB")
        response = self.client.get(self.url, {"q": "QT"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/lab/sample/change_list.html")
        self.assertEqual(list(response.context["cl"].queryset.values_list("pk", flat=True)), [wanted.pk])

    def test_place_filter_not_hijacked(self):
        self.sample()
        self.sample(number="2", placed=False)
        response = self.client.get(self.url, {"storage_place__isnull": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_project_filter_not_hijacked(self):
        self.sample()
        response = self.client.get(self.url, {"project__id__exact": str(self.project.pk)})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["cl"].result_count, 1)

    def test_popup_stays_flat(self):
        self.sample()
        response = self.client.get(self.url, {"_popup": "1"})
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "admin/lab/sample/change_list.html")

    def test_series_search(self):
        self.sample()
        self.sample(series="AB")
        response = self.client.get(self.series_url, {"q": "qt"})
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["series"], "QT")
        self.assertEqual(response.context["total_series"], 2)

    def test_empty_series_search(self):
        self.sample()
        response = self.client.get(self.series_url, {"q": "absent"})
        self.assertEqual(response.context["page"].paginator.count, 0)

    def test_series_pagination(self):
        for index in range(51):
            self.sample(series="R" + str(index))
        first = self.client.get(self.series_url)
        second = self.client.get(self.series_url, {"page": "2"})
        self.assertEqual(len(first.context["rows"]), 50)
        self.assertEqual(len(second.context["rows"]), 1)

    def test_queryset_scope_respected(self):
        self.sample()
        self.sample(series="AB")
        modeladmin = admin.site._registry[Sample]
        with patch.object(modeladmin, "get_queryset", return_value=Sample.objects.filter(series="QT")):
            response = self.client.get(self.url)
            self.assertEqual(response.context["total_samples"], 1)
            self.assertEqual(response.context["total_series"], 1)

    def test_anonymous_denied(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.get(self.series_url).status_code, 302)

    def test_nonstaff_denied(self):
        self.user.is_staff = False
        self.user.save(update_fields=["is_staff"])
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_direct_card_unchanged(self):
        sample = self.sample()
        response = self.client.get(reverse("admin:lab_sample_change", args=[sample.pk]))
        self.assertEqual(response.status_code, 200)

    def test_identifiers_and_parent_preserved(self):
        parent = self.sample(number="1:5")
        child = self.sample(number="12*", parent=parent)
        before = list(Sample.objects.order_by("pk").values())
        self.client.get(self.url)
        self.client.get(self.url, {"series__exact": "QT"})
        self.assertEqual(list(Sample.objects.order_by("pk").values()), before)
        child.refresh_from_db()
        self.assertEqual(child.parent_id, parent.pk)
        self.assertEqual(child.number, "12*")

    def test_no_stock_or_audit_writes_from_view(self):
        from .models import Audit, Lot, StoragePlace
        self.sample()
        before = (Sample.objects.count(), Lot.objects.count(), Movement.objects.count(), Audit.objects.count(), StoragePlace.objects.count())
        self.client.get(self.url)
        self.client.get(self.url, {"all_samples": "1"})
        after = (Sample.objects.count(), Lot.objects.count(), Movement.objects.count(), Audit.objects.count(), StoragePlace.objects.count())
        self.assertEqual(before, after)

    def test_summary_ignores_number_ordering(self):
        self.sample(number="1")
        self.sample(number="2")
        result = list(series_summary(Sample.objects.order_by("number")))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["samples"], 2)

    def test_batch_mixin_and_template_not_replaced(self):
        modeladmin = admin.site._registry[Sample]
        self.assertIn("SampleBatchMixin", [c.__name__ for c in type(modeladmin).__mro__])
        self.assertEqual(modeladmin.change_list_template, "admin/lab/sample/change_list.html")
