from django.contrib import admin
from django.contrib.auth import get_user_model
from django.test import TestCase,RequestFactory
from django.urls import reverse,resolve
from lab.test_stage6a import Fixtures

class FullNavigationTests(Fixtures,TestCase):
    def setUp(self):
        super().setUp()
        self.user=get_user_model().objects.create_user(username="nav8b2_root",password="test-only",is_staff=True,is_superuser=True)
        self.client.force_login(self.user)
    def test_sidebar_keeps_all_permitted_categories_on_model_pages(self):
        for name,active in (("lab_project_changelist","lab_work"),("lab_sample_changelist","lab_samples"),("lab_preparationprotocol_changelist","lab_preparation")):
            request=RequestFactory().get(reverse("admin:"+name));request.user=self.user;request.resolver_match=resolve(request.path)
            context=admin.site.each_context(request)
            self.assertEqual(context["lab8b_section_key"],active)
            self.assertEqual([a["app_label"] for a in context["lab8b_sidebar_apps"]],[a["app_label"] for a in context["available_apps"]])
            self.assertIn("lab_stock",[a["app_label"] for a in context["lab8b_sidebar_apps"]])
    def test_home_and_section_pages_render_full_sidebar(self):
        for url in (reverse("admin:index"),reverse("admin:app_list",args=["lab"]),reverse("admin:lab_section8b",args=["lab_samples"])):
            with self.subTest(url=url):
                response=self.client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertContains(response,'id="nav-sidebar"')
                self.assertEqual([a["app_label"] for a in response.context_data["lab8b_sidebar_apps"]],[a["app_label"] for a in response.context_data["available_apps"]])
                self.assertIn("lab_work",[a["app_label"] for a in response.context_data["lab8b_sidebar_apps"]])
