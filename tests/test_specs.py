import unittest

from pride_radar.specs import parse_specs, parse_publish_time

# Verbatim innerText shape of a real Divar car post, in the order the browser
# renders it. Divar emits a run of labels followed by a run of values, so a naive
# "value is the next line" parser loses everything after the first label.
REAL_PAGE = """پراید ۱۳۱ مدل ۹۴
۳ روز پیش در اهواز، فاز ۱ پادادشهر، خ استادان یکم شرقی
زنگ خطرهای قبل از معامله
اطلاعات تماس
کارکرد
مدل (سال تولید)
رنگ
۳۰۰۰۰۰
۱۳۹۴ - ۲۰۱۵
سفید
برند و مدل
پراید 131 SX
مهلت بیمهٔ شخص ثالث
۸ ماه
گیربکس
دنده‌ای
نوع سوخت
بنزین
قیمت پایه
‏۶۶۰,۰۰۰,۰۰۰ تومان
ارزیابی فروشنده
موتور
سالم
وضعیت شاسی‌ها
سالم و پلمپ
بدنه
خط و خش جزیی
گیربکس
سالم و پلمپ
سایر ویژگی‌ها و امکانات
ارزیابی قیمت
توضیحات
با سلام ، خودرو پراید ۱۳۱ مدل ۹۴ بیمه ۸ ماه"""


class RealPageTests(unittest.TestCase):
    def setUp(self):
        self.s = parse_specs(REAL_PAGE)

    def test_grouped_labels_keep_their_values(self):
        # these three labels are printed before any value appears
        self.assertEqual(self.s.gearbox, "دستی")
        self.assertEqual(self.s.fuel, "بنزین")
        self.assertEqual(self.s.trim, "پراید 131 SX")

    def test_seller_evaluation_block(self):
        self.assertEqual(self.s.engine, "سالم")
        # "سالم و پلمپ" normalises to "سالم"
        self.assertEqual(self.s.chassis, "سالم")
        self.assertEqual(self.s.gearbox_state, "سالم")

    def test_gearbox_type_and_state_are_separate(self):
        # the label appears twice: "دنده‌ای" in the spec table and
        # "سالم و پلمپ" in the seller block. They must not overwrite each other.
        self.assertEqual(self.s.gearbox, "دستی")
        self.assertEqual(self.s.gearbox_state, "سالم")

    def test_minor_scratches_are_not_lost(self):
        # this exact wording was missing and silently dropped to None
        self.assertEqual(self.s.body, "خط و خش جزیی")

    def test_paint_and_insurance(self):
        self.assertEqual(self.s.paint, "سفید")
        self.assertEqual(self.s.insurance, "۸ ماه")

    def test_publish_time_read_from_header_line(self):
        # publish text is normalised to latin digits; the UI renders it back
        self.assertEqual(self.s.published_text, "3 روز پیش")
        self.assertIsNotNone(self.s.published_at)

    def test_nothing_missing(self):
        for name in ("paint", "chassis", "body", "engine", "gearbox", "fuel"):
            self.assertTrue(getattr(self.s, name), f"{name} was not parsed")


class LabelLayoutTests(unittest.TestCase):
    def test_label_value_alternating_still_works(self):
        s = parse_specs("رنگ\nسفید\nگیربکس\nاتومات\nوضعیت شاسی‌ها\nسالم")
        self.assertEqual(s.paint, "سفید")
        self.assertEqual(s.gearbox, "اتومات")
        self.assertEqual(s.chassis, "سالم")

    def test_description_text_is_not_treated_as_a_spec(self):
        s = parse_specs("توضیحات\nخودرو بدون رنگ میباشد\nسینی پشت کمک")
        self.assertIsNone(s.paint)
        self.assertIsNone(s.chassis)


class PublishTimeTests(unittest.TestCase):
    def test_relative_forms(self):
        from datetime import timezone

        from pride_radar.models import utcnow

        for text, hours in (
            ("۱۰ دقیقه پیش", 10 / 60),
            ("یک ربع پیش", 0.25),
            ("نیم ساعت پیش", 0.5),
            ("۳ ساعت پیش", 3),
            ("۲ روز پیش", 48),
            ("۱ هفته پیش", 168),
        ):
            when, label = parse_publish_time(text)
            self.assertIsNotNone(when, text)
            self.assertIsNotNone(label, text)
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            delta = abs((utcnow() - when).total_seconds() / 3600 - hours)
            self.assertLess(delta, 0.05, f"{text} -> {when}")

    def test_no_timestamp(self):
        when, label = parse_publish_time("پراید ۱۳۱ مدل ۹۴")
        self.assertIsNone(when)
        self.assertIsNone(label)


if __name__ == "__main__":
    unittest.main()
