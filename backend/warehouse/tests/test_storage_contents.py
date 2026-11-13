from django.test import SimpleTestCase
from django.http import QueryDict
from django.core.paginator import Paginator
from lab.storage8e_admin import page_size, selected_columns, page_links, COLUMNS

class Storage8EHelpersTests(SimpleTestCase):
    def test_sizes(self):
        for size in (25, 50, 100):
            self.assertEqual(page_size(str(size)), size)
    def test_invalid_sizes(self):
        for value in (None, "", "0", "-1", "999999", "x"):
            self.assertEqual(page_size(value), 50)
    def test_default_columns(self):
        for group in COLUMNS:
            self.assertEqual(selected_columns(QueryDict(""), group), COLUMNS[group])
    def test_name_required(self):
        self.assertEqual(selected_columns(QueryDict("columns_set=1"), "lots"), (("name", "Продукт"),))
    def test_unknown_columns_ignored(self):
        result = selected_columns(QueryDict("columns_set=1&lots_cols=password&lots_cols=code"), "lots")
        self.assertEqual([key for key, label in result], ["name", "code"])
    def test_code_last(self):
        for group in ("lots", "blocks"):
            self.assertEqual(COLUMNS[group][-1][0], "code")
    def test_links_preserve_filters_and_lists(self):
        params = QueryDict("q=DNA&lots_cols=name&lots_cols=code&sample_page=3&lots_size=25")
        original = params.urlencode()
        links = page_links(params, Paginator(list(range(6)), 2).page(2), "lot_page")
        for label, expected in (("previous", "1"), ("next", "3")):
            parsed = QueryDict(links[label][1:])
            self.assertEqual(parsed["lot_page"], expected)
            self.assertEqual(parsed["sample_page"], "3")
            self.assertEqual(parsed.getlist("lots_cols"), ["name", "code"])
            self.assertEqual(parsed["q"], "DNA")
        self.assertEqual(params.urlencode(), original)
    def test_single_page_links_empty(self):
        self.assertEqual(page_links(QueryDict(""), Paginator([], 50).page(1), "lot_page"), {})
