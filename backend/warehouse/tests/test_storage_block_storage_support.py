import unittest
from types import SimpleNamespace
from lab.storage8d_support import occupancy,block_code_id

class Storage8DSupportTests(unittest.TestCase):
    def test_cyrillic_code(self):self.assertEqual(block_code_id("ИБ-000123"),123)
    def test_latin_code(self):self.assertEqual(block_code_id("IB-000123"),123)
    def test_lowercase_and_spaces(self):self.assertEqual(block_code_id("  иб-0001  "),1)
    def test_plain_id(self):self.assertEqual(block_code_id("17"),17)
    def test_invalid_codes(self):
        for q in ("abc","ИБ-0","-1","ИБ-abc","IB-12x","1.5",""):
            self.assertIsNone(block_code_id(q))
    def test_bigint_boundary(self):
        self.assertEqual(block_code_id("9223372036854775807"),9223372036854775807)
        self.assertIsNone(block_code_id("9223372036854775808"))
    def test_very_long_input(self):self.assertIsNone(block_code_id("9"*10000))
    def test_empty_occupancy(self):self.assertEqual(occupancy([]),{"filled":0,"sample_wells":0,"unique_samples":0,"controls":0,"raw":0})
    def test_repeated_samples_not_duplicated(self):
        rows=[SimpleNamespace(kind="sample",sample_id=1),SimpleNamespace(kind="sample",sample_id=1)]
        value=occupancy(rows);self.assertEqual(value["sample_wells"],2);self.assertEqual(value["unique_samples"],1)
    def test_separate_states(self):
        rows=[SimpleNamespace(kind="sample",sample_id=1),SimpleNamespace(kind="water",sample_id=None),SimpleNamespace(kind="unregistered",sample_id=None)]
        self.assertEqual(occupancy(rows),{"filled":3,"sample_wells":1,"unique_samples":1,"controls":1,"raw":1})
    def test_generator_input(self):
        result=occupancy(SimpleNamespace(kind="water",sample_id=None) for _ in range(3));self.assertEqual(result["controls"],3)
    def test_full_plate_not_96_sample_cards(self):
        result=occupancy(SimpleNamespace(kind="sample",sample_id=1) for _ in range(96));self.assertEqual(result["filled"],96);self.assertEqual(result["unique_samples"],1)
