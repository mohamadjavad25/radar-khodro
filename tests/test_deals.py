import unittest
from datetime import timedelta

from pride_radar.deals import (
    BUY,
    PASS,
    WATCH,
    SELL_THROUGH_RATIO,
    DealSettings,
    condition_discount,
    evaluate_deal,
    rank_results,
    resale_estimate,
    summarize,
)
from pride_radar.market import build_snapshot
from pride_radar.models import Listing, Price, utcnow


def car(**kw) -> Listing:
    base = dict(
        listing_id=kw.pop("listing_id", "x"),
        title="پراید ۱۳۱",
        price=Price(toman=kw.pop("price", 400_000_000)),
        vehicle_path="pride/131",
        vehicle_name="Pride 131",
        brand_path="pride",
        brand_fa="پراید",
        year=kw.pop("year", 1399),
        # under the free-mileage threshold so a clean car has no discount at all
        mileage_km=kw.pop("mileage", 50_000),
        city="ahvaz",
        published_at=kw.pop("published_at", utcnow()),
    )
    base.update(kw)
    return Listing(**base)


def clean(price=400_000_000, **kw) -> Listing:
    return car(
        price=price,
        body_condition=kw.pop("body_condition", "بی‌خط و خش"),
        chassis=kw.pop("chassis", "سالم"),
        engine=kw.pop("engine", "سالم"),
        specs_fetched=True,
        **kw,
    )


LOOSE = DealSettings(
    min_profit_toman=1,
    min_roi=0.0,
    max_age_hours=999,
    require_clean_body=False,
    require_sound_chassis=False,
    require_known_condition=False,
    exclude_business=False,
)


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        rows = [clean(price=500_000_000, listing_id=f"m{i}") for i in range(12)]
        self.snapshot = build_snapshot(rows)


class ConditionDiscountTests(unittest.TestCase):
    def test_clean_car_has_no_discount(self):
        factor, good, bad = condition_discount(clean())
        self.assertEqual(factor, 1.0)
        self.assertIn("بدنه بدون رنگ", good)
        self.assertEqual(bad, [])

    def test_painted_body_reduces_resale(self):
        factor, _good, bad = condition_discount(clean(body_condition="رنگ‌شده"))
        self.assertLess(factor, 1.0)
        self.assertIn("بدنه رنگ‌شده", bad)

    def test_chassis_damage_costs_more_than_paint(self):
        paint, _, _ = condition_discount(clean(body_condition="رنگ‌شده"))
        chassis, _, _ = condition_discount(clean(chassis="آسیب‌دارد"))
        self.assertLess(chassis, paint)

    def test_high_mileage_reduces_resale(self):
        low, _, _ = condition_discount(clean(mileage=50_000))
        high, _, _ = condition_discount(clean(mileage=250_000))
        self.assertLess(high, low)

    def test_unknown_condition_is_penalised(self):
        unknown = car(specs_fetched=False)
        factor, _good, bad = condition_discount(unknown)
        self.assertLess(factor, 1.0)
        self.assertIn("وضعیت بدنه نامشخص", bad)


class ResaleEstimateTests(SnapshotTests):
    def test_clean_car_sells_below_median_asking_price(self):
        # you cannot bank the median asking price; you must price under it
        resale, factor = resale_estimate(clean(), self.snapshot)
        self.assertEqual(factor, 1.0)
        self.assertEqual(resale, int(self.snapshot.overall.median * SELL_THROUGH_RATIO))
        self.assertLess(resale, self.snapshot.overall.median)

    def test_damaged_car_resells_below_median(self):
        resale, factor = resale_estimate(clean(chassis="آسیب‌دارد"), self.snapshot)
        self.assertLess(resale, int(self.snapshot.overall.median * SELL_THROUGH_RATIO))
        self.assertLess(factor, 1.0)


class VerdictTests(SnapshotTests):
    def _settings(self, **kw):
        base = dict(
            min_profit_toman=30_000_000,
            min_roi=0.06,
            max_age_hours=24,
            require_clean_body=True,
            require_sound_chassis=True,
            require_known_condition=True,
            exclude_business=True,
        )
        base.update(kw)
        return DealSettings(**base)

    def test_cheap_clean_car_is_buy(self):
        r = evaluate_deal(clean(price=350_000_000), self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, BUY)
        self.assertGreater(r.deal.profit_toman, 0)
        self.assertEqual(r.deal.blockers, ())

    def test_full_price_car_has_no_profit(self):
        r = evaluate_deal(clean(price=520_000_000), self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)
        self.assertIsNone(r.deal.profit_toman)

    def test_painted_car_is_blocked(self):
        r = evaluate_deal(
            clean(price=300_000_000, body_condition="رنگ‌شده"),
            self.snapshot,
            self._settings(),
        )
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("بدنه" in b for b in r.deal.blockers))

    def test_chassis_damage_is_blocked(self):
        r = evaluate_deal(
            clean(price=300_000_000, chassis="آسیب‌دارد"),
            self.snapshot,
            self._settings(),
        )
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("شاسی" in b for b in r.deal.blockers))

    def test_stale_listing_is_blocked(self):
        stale = clean(price=300_000_000)
        stale.published_at = utcnow() - timedelta(hours=72)
        r = evaluate_deal(stale, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("قدیمی" in b for b in r.deal.blockers))

    def test_unread_specs_are_blocked_when_required(self):
        blind = clean(price=300_000_000)
        blind.specs_fetched = False
        blind.body_condition = None
        blind.chassis = None
        r = evaluate_deal(blind, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)
        self.assertIn("مشخصات بررسی نشده", r.deal.blockers)

    def test_business_listing_is_blocked(self):
        r = evaluate_deal(
            clean(price=300_000_000, business=True), self.snapshot, self._settings()
        )
        self.assertEqual(r.deal.verdict, PASS)
        self.assertIn("آگهی مغازه‌ای", r.deal.blockers)

    def test_suspicious_price_is_not_an_opportunity(self):
        r = evaluate_deal(clean(price=100_000_000), self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("طعمه" in b for b in r.deal.blockers))

    def test_small_profit_is_watch_not_buy(self):
        r = evaluate_deal(
            clean(price=450_000_000), self.snapshot, self._settings()
        )
        self.assertEqual(r.deal.verdict, WATCH)

    def test_profit_does_not_assume_median_instant_resale(self):
        # a car bought at exactly the median asking price must not look profitable
        r = evaluate_deal(
            clean(price=self.snapshot.overall.median), self.snapshot, self._settings()
        )
        self.assertIsNone(r.deal.profit_toman)
        self.assertEqual(r.deal.verdict, PASS)

    def test_unknown_body_is_never_a_confident_buy(self):
        # Seller did not declare paintwork: worth calling, not an automatic yes.
        blind = clean(price=300_000_000)
        blind.body_condition = None
        r = evaluate_deal(blind, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, WATCH)
        self.assertTrue(any("بدنه" in x for x in r.deal.reasons))
        self.assertEqual(r.deal.blockers, ())

    def test_unknown_chassis_is_never_a_confident_buy(self):
        blind = clean(price=300_000_000)
        blind.chassis = None
        r = evaluate_deal(blind, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, WATCH)
        self.assertTrue(any("شاسی" in x for x in r.deal.reasons))

    def test_known_paintwork_is_still_a_hard_reject(self):
        painted = clean(price=300_000_000, body_condition="رنگ‌شده")
        r = evaluate_deal(painted, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)
        self.assertTrue(any("بدنه" in b for b in r.deal.blockers))

    def test_unknown_does_not_reject_when_gate_is_off(self):
        loose = self._settings(require_clean_body=False, require_sound_chassis=False)
        blind = clean(price=300_000_000)
        blind.body_condition = None
        blind.chassis = None
        r = evaluate_deal(blind, self.snapshot, loose)
        self.assertEqual(r.deal.verdict, BUY)

    def test_reasons_explain_the_profit(self):
        r = evaluate_deal(clean(price=350_000_000), self.snapshot, self._settings())
        joined = " ".join(r.deal.reasons)
        self.assertIn("سود", joined)


class MinorScratchTests(SnapshotTests):
    """Cosmetics are not paintwork; a repaint is what kills resale value."""

    def _settings(self, **kw):
        base = dict(
            min_profit_toman=30_000_000,
            min_roi=0.06,
            max_age_hours=24,
            require_clean_body=True,
            require_sound_chassis=True,
            require_known_condition=True,
            exclude_business=True,
        )
        base.update(kw)
        return DealSettings(**base)

    def test_minor_scratches_still_tradeable(self):
        car_ = clean(price=400_000_000, body_condition="خط و خش جزیی")
        r = evaluate_deal(car_, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, BUY)
        self.assertIn("بدنه بدون رنگ", r.good)

    def test_minor_scratches_cost_a_little(self):
        pristine, _, _ = condition_discount(clean())
        scratched, _, _ = condition_discount(
            clean(body_condition="خط و خش جزیی")
        )
        self.assertLess(scratched, pristine)
        self.assertGreater(scratched, 0.98)

    def test_repaint_is_rejected(self):
        car_ = clean(price=400_000_000, body_condition="رنگ‌شده")
        r = evaluate_deal(car_, self.snapshot, self._settings())
        self.assertEqual(r.deal.verdict, PASS)


class RankingTests(SnapshotTests):
    def test_buys_come_before_watches(self):
        results = [
            evaluate_deal(clean(price=485_000_000, listing_id="w"), self.snapshot, LOOSE),
            evaluate_deal(clean(price=300_000_000, listing_id="b"), self.snapshot, LOOSE),
        ]
        ranked = rank_results(results, include_pass=True)
        self.assertEqual(ranked[0].deal.listing_id, "b")

    def test_highest_profit_first(self):
        results = [
            evaluate_deal(clean(price=450_000_000, listing_id="a"), self.snapshot, LOOSE),
            evaluate_deal(clean(price=300_000_000, listing_id="b"), self.snapshot, LOOSE),
        ]
        ranked = rank_results(results, include_pass=True)
        profits = [r.deal.profit_toman or 0 for r in ranked]
        self.assertEqual(profits, sorted(profits, reverse=True))

    def test_pass_rows_hidden_by_default(self):
        results = [
            evaluate_deal(clean(price=520_000_000, listing_id="p"), self.snapshot, LOOSE)
        ]
        self.assertEqual(rank_results(results), [])


class SummaryTests(SnapshotTests):
    def test_counts_add_up(self):
        results = [
            evaluate_deal(clean(price=300_000_000, listing_id="b"), self.snapshot, LOOSE),
            evaluate_deal(clean(price=485_000_000, listing_id="w"), self.snapshot, LOOSE),
            evaluate_deal(clean(price=520_000_000, listing_id="p"), self.snapshot, LOOSE),
        ]
        totals = summarize(results)
        self.assertEqual(totals["considered"], 3)
        self.assertEqual(totals["buy"] + totals["watch"] + totals["pass"], 3)


if __name__ == "__main__":
    unittest.main()
