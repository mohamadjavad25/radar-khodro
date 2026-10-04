import unittest

from pride_radar import text as t


class NormalizeTests(unittest.TestCase):
    def test_persian_digits_to_latin(self):
        self.assertEqual(t.normalize_digits("۱۳۹۸"), "1398")

    def test_arabic_yeh_and_kaf_unified(self):
        self.assertEqual(t.normalize("پرايد"), "پراید")

    def test_zwnj_becomes_space(self):
        self.assertEqual(t.normalize("ال‌پی"), "ال پی")

    def test_whitespace_collapsed(self):
        self.assertEqual(t.normalize("  پراید   ۱۳۱  "), "پراید 131")


class ParseNumberTests(unittest.TestCase):
    def test_grouped_digits(self):
        self.assertEqual(t.parse_number("250,000,000"), 250_000_000.0)

    def test_decimal(self):
        self.assertEqual(t.parse_number("2.5"), 2.5)

    def test_persian_digits(self):
        self.assertEqual(t.parse_number("۱۳۹۸"), 1398.0)

    def test_thousands_separator_u066c(self):
        self.assertEqual(t.parse_number("250\u066c000"), 250_000.0)

    def test_garbage(self):
        self.assertIsNone(t.parse_number("abc"))


class ParsePriceTests(unittest.TestCase):
    def test_million_with_currency(self):
        self.assertEqual(t.parse_price("۲۵۰ میلیون تومان")[0], 250_000_000)

    def test_million_bare(self):
        self.assertEqual(t.parse_price("قیمت ۲۵۰ میلیون")[0], 250_000_000)

    def test_full_digit_toman(self):
        self.assertEqual(t.parse_price("250,000,000 تومان")[0], 250_000_000)

    def test_billion(self):
        self.assertEqual(t.parse_price("۲.۵ میلیارد تومان")[0], 2_500_000_000)

    def test_rial_divided_by_ten(self):
        self.assertEqual(t.parse_price("250,000,000 ریال")[0], 25_000_000)

    def test_negotiable_without_number(self):
        value, negotiable = t.parse_price("قابل توافق")
        self.assertIsNone(value)
        self.assertTrue(negotiable)

    def test_call_for_price(self):
        value, negotiable = t.parse_price("تماس بگیرید")
        self.assertIsNone(value)
        self.assertTrue(negotiable)

    def test_mileage_is_not_a_price(self):
        self.assertIsNone(t.parse_price("کارکرد ۹۰ هزار کیلومتر")[0])

    def test_model_and_year_are_not_prices(self):
        self.assertIsNone(t.parse_price("پراید ۱۳۱ مدل ۱۳۹۸")[0])

    def test_empty(self):
        self.assertEqual(t.parse_price(None), (None, False))

    def test_picks_largest_amount(self):
        value, _ = t.parse_price("قیمت ۲۵۰ میلیون معاوضه با وانت ۵۰ میلیون")
        self.assertEqual(value, 250_000_000)


class FormatTests(unittest.TestCase):
    def test_format_million(self):
        self.assertEqual(t.format_toman(250_000_000), "250 میلیون")

    def test_format_billion(self):
        self.assertEqual(t.format_toman(2_500_000_000), "2.5 میلیارد")

    def test_format_none(self):
        self.assertEqual(t.format_toman(None), "—")


if __name__ == "__main__":
    unittest.main()
