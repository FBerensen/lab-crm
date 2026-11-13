from html.parser import HTMLParser
from django.test import TestCase
from django.urls import reverse
from lab.test_stage6a import Fixtures

class BreadcrumbParser(HTMLParser):
    def __init__(self):
        super().__init__();self.depth=0;self.current=None;self.links=[];self.found=False
    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag=="div":
            if self.depth:self.depth+=1
            elif "breadcrumbs" in attrs.get("class","").split():self.depth=1;self.found=True
        if self.depth and tag=="a":self.current={"href":attrs.get("href",""),"text":""}
    def handle_data(self,data):
        if self.depth and self.current is not None:self.current["text"]+=data
    def handle_endtag(self,tag):
        if tag=="a" and self.current is not None:
            self.current["text"]=self.current["text"].strip();self.links.append(self.current);self.current=None
        if tag=="div" and self.depth:self.depth-=1

class HomeBreadcrumbTests(Fixtures,TestCase):
    def setUp(self):
        super().setUp();self.client.force_login(self.executor)
    def parse(self,response):
        parser=BreadcrumbParser();parser.feed(response.content.decode());return parser
    def test_main_home_has_start_link_to_lab_and_full_sidebar(self):
        response=self.client.get(reverse("admin:index"))
        self.assertEqual(response.status_code,200)
        parser=self.parse(response);self.assertTrue(parser.found)
        self.assertIn({"href":reverse("admin:app_list",args=["lab"]),"text":"Начало"},parser.links)
        self.assertContains(response,'id="nav-sidebar"')
        self.assertContains(response,'data-labcrm-home-breadcrumb="8b3"')
    def test_existing_model_and_app_breadcrumbs_keep_home_link(self):
        for url in (reverse("admin:app_list",args=["lab"]),reverse("admin:lab_project_changelist")):
            with self.subTest(url=url):
                response=self.client.get(url);self.assertEqual(response.status_code,200)
                parser=self.parse(response);self.assertTrue(parser.found)
                self.assertIn(reverse("admin:index"),[link["href"] for link in parser.links])
                self.assertNotContains(response,'data-labcrm-home-breadcrumb="8b3"')
