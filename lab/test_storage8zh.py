import unittest
from decimal import Decimal, localcontext, Inexact, Rounded
from .storage8zh_support import display_quantity


class Storage8ZHQuantityTests(unittest.TestCase):
    def test_ml_boundary(self):
        self.assertEqual(display_quantity(Decimal("999.999"), "мл"), "999,999 мл")
        self.assertEqual(display_quantity(Decimal("1000"), "мл"), "1 л")
        self.assertEqual(display_quantity(Decimal("1500"), "мл"), "1,5 л")
    def test_l_boundary(self):
        self.assertEqual(display_quantity(Decimal("0.999999"), "л"), "999,999 мл")
        self.assertEqual(display_quantity(Decimal("1"), "л"), "1 л")
    def test_g_boundary(self):
        self.assertEqual(display_quantity(Decimal("999.999"), "г"), "999,999 г")
        self.assertEqual(display_quantity(Decimal("1000"), "г"), "1 кг")
    def test_kg_boundary(self):
        self.assertEqual(display_quantity(Decimal("0.25"), "кг"), "250 г")
        self.assertEqual(display_quantity(Decimal("1"), "кг"), "1 кг")
    def test_micro_units_unchanged(self):
        self.assertEqual(display_quantity(Decimal("1000000"), "мкл"), "1000000 мкл")
        self.assertEqual(display_quantity(Decimal("1000000"), "мкг"), "1000000 мкг")
    def test_count_units(self):
        self.assertEqual(display_quantity(1000, "шт"), "1000 шт")
        self.assertEqual(display_quantity(Decimal("1.50"), "упак"), "1,5 упак")
    def test_zero(self):
        for unit, expected in (("л", "мл"), ("кг", "г"), ("мл", "мл"), ("г", "г")):
            self.assertEqual(display_quantity(Decimal("-0.000"), unit), "0 " + expected)
    def test_small_nonzero(self):
        self.assertEqual(display_quantity(Decimal("0.000000001"), "л"), "0,000001 мл")
        self.assertEqual(display_quantity(Decimal("0.000000001"), "г"), "0,000000001 г")
    def test_invalid(self):
        for value in (None, "bad", Decimal("NaN"), Decimal("Infinity")):
            self.assertEqual(display_quantity(value, "л"), "—")
    def test_trailing_zeros_and_negative(self):
        self.assertEqual(display_quantity(Decimal("12.34000"), "мл"), "12,34 мл")
        self.assertEqual(display_quantity(Decimal("-1500"), "мл"), "-1,5 л")
    def test_independent_values(self):
        self.assertEqual([display_quantity(Decimal(v), "л") for v in ("1.5", "0.2", "1.3")],
                         ["1,5 л", "200 мл", "1,3 л"])
    def test_exact_under_small_context(self):
        value = Decimal("123456789012345678901234567890.123456789")
        with localcontext() as ctx:
            ctx.prec = 3
            ctx.traps[Inexact] = True
            ctx.traps[Rounded] = True
            flags = dict(ctx.flags)
            self.assertEqual(display_quantity(value, "мл"), "123456789012345678901234567,890123456789 л")
            self.assertEqual(dict(ctx.flags), flags)
            self.assertEqual(ctx.prec, 3)
