from unittest.mock import patch
from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase,RequestFactory
from django.urls import reverse,resolve
from lab.test_stage6a import Fixtures
from lab.models import Sample,Project,TaskEvent,PreparationProtocol,Lot,Movement,Audit
from lab.navigation8b import SECTION_KEYS

class NavigationTests(Fixtures,TestCase):
    def setUp(self):
        super().setUp()
        self.site=admin.site
        self.root=get_user_model().objects.create_user(username="nav8b_root",password="test-only",is_staff=True,is_superuser=True)
        self.client.force_login(self.root)
    def req(self,url=None,user=None):
        request=RequestFactory().get(url or reverse("admin:index"));request.user=user or self.root
        request.resolver_match=resolve(request.path)
        return request
    def groups(self,user=None):return [a for a in self.site.get_app_list(self.req(user=user)) if a["app_label"] in SECTION_KEYS]
    def test_group_links_are_distinct_section_urls(self):
        apps=self.groups();self.assertGreaterEqual(len(apps),5)
        urls=[a["app_url"] for a in apps]
        self.assertEqual(len(urls),len(set(urls)))
        for app in apps:self.assertEqual(app["app_url"],reverse("admin:lab_section8b",args=[app["app_label"]]))
    def test_home_index_contains_section_links(self):
        response=self.client.get(reverse("admin:index"));self.assertEqual(response.status_code,200)
        for app in self.groups():self.assertContains(response,app["app_url"])
    def test_lab_index_contains_section_links(self):
        response=self.client.get(reverse("admin:app_list",args=["lab"]));self.assertEqual(response.status_code,200)
        for app in self.groups():self.assertContains(response,app["app_url"])
    def test_all_visible_sections_open(self):
        for app in self.groups():
            with self.subTest(section=app["app_label"]):
                response=self.client.get(app["app_url"]);self.assertEqual(response.status_code,200)
                self.assertEqual(response.context_data["section"]["app_label"],app["app_label"])
                self.assertEqual(len(response.context_data["section_apps"]),1)
    def test_section_only_contains_its_models(self):
        url=reverse("admin:lab_section8b",args=["lab_samples"])
        response=self.client.get(url)
        names={m["object_name"] for m in response.context_data["section"]["models"]}
        self.assertEqual(names,{"Sample", "SourceBlock"})
    def test_project_sidebar_is_work_category(self):
        request=self.req(reverse("admin:lab_project_changelist"))
        context=self.site.each_context(request)
        self.assertEqual(context["lab8b_section_key"],"lab_work")
        self.assertEqual([a["app_label"] for a in context["lab8b_sidebar_apps"]],[a["app_label"] for a in context["available_apps"]])
        group=next(a for a in context["lab8b_sidebar_apps"] if a["app_label"]=="lab_work")
        names={m["object_name"] for m in group["models"]}
        self.assertIn("TaskEvent",names)
        self.assertNotIn("Lot",names)
    def test_task_event_sidebar_is_work_category(self):
        context=self.site.each_context(self.req(reverse("admin:lab_taskevent_changelist")))
        self.assertEqual(context["lab8b_section_key"],"lab_work")
    def test_preparation_protocol_sidebar(self):
        context=self.site.each_context(self.req(reverse("admin:lab_preparationprotocol_changelist")))
        self.assertEqual(context["lab8b_section_key"],"lab_preparation")
    def test_sample_change_sidebar_and_js_context(self):
        response=self.client.get(reverse("admin:lab_sample_change",args=[self.sample.pk]))
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context_data["lab8b_section_key"],"lab_samples")
        self.assertContains(response,'id="nav-sidebar"')
        self.assertContains(response,'id="lab8b-navigation-data"')
        from django.templatetags.static import static
        self.assertContains(response, static("lab/js/navigation8b_init.js"))
    def test_custom_series_page_keeps_category(self):
        response=self.client.get(reverse("admin:lab_sample_series"));self.assertEqual(response.status_code,200)
        self.assertEqual(response.context_data["lab8b_section_key"],"lab_samples")
        self.assertContains(response,'id="nav-sidebar"')
    def test_auth_app_url_not_changed(self):
        apps=self.site.get_app_list(self.req(),"auth")
        self.assertEqual(len(apps),1);self.assertEqual(apps[0]["app_url"],reverse("admin:app_list",args=["auth"]))
    def test_permission_filtering_preserved(self):
        request=self.req(user=self.executor)
        old=self.site._navigation8b_previous_get_app_list(request)
        new=self.site.get_app_list(request)
        flatten=lambda apps:sorted((m["object_name"],tuple(sorted(m["perms"].items())),m.get("admin_url"),m.get("add_url")) for a in apps for m in a["models"])
        self.assertEqual(flatten(old),flatten(new))
    def test_hidden_category_denied(self):
        ma=self.site._registry[Sample]
        with patch.object(ma,"has_module_permission",return_value=False):
            request=self.req();apps=self.site.get_app_list(request)
            self.assertNotIn("lab_samples",{a["app_label"] for a in apps})
            response=self.client.get(reverse("admin:lab_section8b",args=["lab_samples"]))
            self.assertEqual(response.status_code,403)
    def test_unknown_category_404(self):
        self.assertEqual(self.client.get(reverse("admin:lab_section8b",args=["unknown"])).status_code,404)
    def test_post_not_allowed(self):
        self.assertEqual(self.client.post(reverse("admin:lab_section8b",args=["lab_work"]),{}).status_code,405)
    def test_anonymous_redirected(self):
        self.client.logout()
        response=self.client.get(reverse("admin:lab_section8b",args=["lab_work"]))
        self.assertEqual(response.status_code,302);self.assertIn("login",response.url)
    def test_navigation_does_not_change_lab_data(self):
        before=(Sample.objects.count(),Project.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance())
        for key in ("lab_work","lab_samples","lab_stock"):
            self.assertEqual(self.client.get(reverse("admin:lab_section8b",args=[key])).status_code,200)
        self.assertEqual(before,(Sample.objects.count(),Project.objects.count(),Lot.objects.count(),Movement.objects.count(),Audit.objects.count(),self.balance()))
    def test_existing_model_edit_permissions_unchanged(self):
        request=self.req(user=self.student)
        self.assertFalse(self.site._registry[Sample].has_change_permission(request,self.sample))
        request=self.req(user=self.executor)
        self.assertTrue(self.site._registry[Sample].has_change_permission(request,self.sample))
