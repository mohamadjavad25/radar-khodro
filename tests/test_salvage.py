import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from pride_radar.deals import (
    BUY,
    PASS,
    WATCH,
    DealSettings,
    condition_discount,
    evaluate_deal,
    salvage_reasons,
)
from pride_radar.market import build_snapshot
from pride_radar.models import Listing, Price, utcnow


def car(title="پراید ۱۳۱", **kw) -> Listing:
    base = dict(
        listing_id=kw.pop("listing_id", "x"),
        title=title,
        description=kw.pop("description", "۱ ساعت پیش در اهواز"),
        price=Price(toman=kw.pop("price", 400_000_000)),
        vehicle_path="pride/131",
        vehicle_name="Pride 131",
        brand_path="pride",
        brand_fa="پراید",
        year=kw.pop("year", 1399),
        mileage_km=kw.pop("mileage", 50_000),
        city="ahvaz",
        published_at=utcnow(),
        body_condition=kw.pop("body_condition", "بی‌خط و خش"),
        chassis=kw.pop("chassis", "سالم"),
        engine=kw.pop("engine", "سالم"),
        specs_fetched=True,
    )
    base.update(kw)
    return Listing(**base)


SNAPSHOT = build_snapshot(
    [car(price=500_000_000, listing_id=f"m{i}") for i in range(12)]
)

SETTINGS = DealSettings(
    min_profit_toman=30_000_000,
    min_roi=0.06,
    max_age_hours=24,
)


class SalvageMarkerTests(unittest.TestCase):
    def test_accident_is_found(self):
        self.assertIn("تصادف", salvage_reasons(car(title="پراید131اخرسال94تصادفی")))

    def test_rollover_is_found(self):
        self.assertTrue(salvage_reasons(car(title="پراید ۱۳۱ اوت گاز")))

    def test_writeoff_is_found(self):
        self.assertTrue(salvage_reasons(car(title="پراید اوراق")))

    def test_flood_is_found(self):
        self.assertTrue(salvage_reasons(car(title="پراید ۱۳۱ سیل زده")))

    def test_fire_is_found(self):
        self.assertTrue(salvage_reasons(car(title="پراید ۱۳۱ آتش سوزی")))

    def test_missing_engine_is_found(self):
        reasons = salvage_reasons(car(title="پراید ۱۳۱ بدون موتور"))
        self.assertIn("بدون موتور", reasons)

    def test_negotiable_price_is_flagged(self):
        self.assertTrue(salvage_reasons(car(title="پراید ۱۳۱ قیمت توافقی")))

    def test_clean_car_has_no_markers(self):
        self.assertEqual(salvage_reasons(car(title="پراید ۱۳۱ مدل ۹۹ بی‌خط")), [])

    def test_arabic_ya_and_kaf_variants_still_match(self):
        # sellers type ي/ك instead of ی/ک
        self.assertTrue(salvage_reasons(car(title="پرايد ١٣١ شاسى ضربه")))

    def test_english_digits_still_match(self):
        self.assertTrue(salvage_reasons(car(title="پراید 131 تصادف")))

    def test_markers_in_description_count(self):
        self.assertTrue(
            salvage_reasons(car(title="پراید ۱۳۱", description="خودرو تصادفی بوده"))
        )

    def test_manual_gearbox_is_not_damage(self):
        # regression: "دستی" is the gearbox, must never be read as damage
        self.assertEqual(salvage_reasons(car(title="پراید ۱۳۱ گیربکس دستی")), [])

    def test_gas_fuel_is_not_damage(self):
        self.assertEqual(salvage_reasons(car(title="پراید ۱۳۱ دوگانه گاز")), [])


class SalvageGateTests(unittest.TestCase):
    def test_damaged_car_is_rejected(self):
        r = evaluate_deal(
            car(title="پراید131اخرسال94تصادفی", price=150_000_000),
            SNAPSHOT,
            SETTINGS,
        )
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("آسیب‌دیده" in b for b in r.deal.blockers))

    def test_damage_is_rejected_even_when_cheap(self):
        # a cheap damaged car would otherwise look like the best deal on the page
        r = evaluate_deal(
            car(title="پراید اوت گاز", price=120_000_000), SNAPSHOT, SETTINGS
        )
        self.assertEqual(r.deal.verdict, PASS)

    def test_damage_visible_in_details(self):
        r = evaluate_deal(
            car(title="پراید اوراق", price=100_000_000), SNAPSHOT, SETTINGS
        )
        self.assertTrue(any("اوراق" in b for b in r.bad))

    def test_gate_can_be_turned_off(self):
        loose = DealSettings(
            min_profit_toman=30_000_000,
            min_roi=0.06,
            max_age_hours=24,
            exclude_salvage=False,
        )
        damaged = car(title="پراید131اخرسال94تصادفی", price=250_000_000)
        r = evaluate_deal(damaged, SNAPSHOT, loose)
        # the blocker is gone...
        self.assertFalse(any("آسیب‌دیده" in b for b in r.deal.blockers))
        # ...but the damage still shows up as a risk, and still hits resale value
        self.assertTrue(any("تصادف" in b for b in r.bad))
        self.assertLess(r.deal.discount_factor, 1.0)

    def test_damage_is_a_blocker_when_gate_is_on(self):
        strict = DealSettings(
            min_profit_toman=30_000_000,
            min_roi=0.06,
            max_age_hours=24,
            exclude_salvage=True,
        )
        r = evaluate_deal(
            car(title="پراید131اخرسال94تصادفی", price=250_000_000),
            SNAPSHOT,
            strict,
        )
        self.assertTrue(any("آسیب‌دیده" in b for b in r.deal.blockers))

    def test_damage_lowers_resale_value(self):
        clean = condition_discount(car())[0]
        damaged = condition_discount(car(title="پراید اوت گاز"))[0]
        self.assertLess(damaged, clean)

    def test_clean_car_is_unaffected(self):
        r = evaluate_deal(car(price=300_000_000), SNAPSHOT, SETTINGS)
        self.assertEqual(r.deal.verdict, BUY)


if __name__ == "__main__":
    unittest.main()
