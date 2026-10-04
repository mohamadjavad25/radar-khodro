import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from pride_radar import catalog as cat
from pride_radar.config import Rule, VehicleTarget, load_config
from pride_radar.db import Database
from pride_radar.market import build_snapshot, compare_to_market, suggest_offer
from pride_radar.models import Listing, Price, utcnow
from pride_radar.rules import check_rule, evaluate, passes_global_filters, score_listing
from pride_radar.config import FilterConfig

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ROOT / "data" / "divar-filters.json"

WEIGHTS = {"price": 0.4, "year": 0.25, "mileage": 0.2, "model": 0.15}


def load_cat() -> cat.Catalog:
    if FILTERS.is_file():
        return cat.build_from_filters(json.loads(FILTERS.read_text(encoding="utf-8")))
    return cat.Catalog()


def make_listing(
    listing_id="x1",
    price=250_000_000,
    vehicle="pride/131",
    year=1398,
    mileage=90_000,
    business=False,
    age_hours=0,
    **specs,
):
    return Listing(
        listing_id=listing_id,
        title="آگهی نمونه",
        price=Price(toman=price),
        vehicle_path=vehicle,
        vehicle_name=vehicle,
        vehicle_fa=vehicle,
        brand_path=(vehicle or "").split("/")[0] or None,
        brand_fa=(vehicle or "").split("/")[0] or None,
        year=year,
        mileage_km=mileage,
        city="ahvaz",
        business=business,
        first_seen_at=utcnow() - timedelta(hours=age_hours),
        last_seen_at=utcnow() - timedelta(hours=age_hours),
        **specs,
    )


class CatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_cat()

    def setUp(self):
        if not len(self.cat):
            self.skipTest("catalog not built (run fetch once)")

    def test_size(self):
        self.assertGreater(len(self.cat), 1000)
        self.assertGreater(len(self.cat.brands), 50)

    def test_brand_lookup(self):
        for name in ("pride", "peugeot", "dena", "samand", "hyundai"):
            with self.subTest(name=name):
                self.assertIsNotNone(self.cat.resolve_path(name))

    def test_persian_match_pride(self):
        m = self.cat.match_in("پراید ۱۳۱ مدل ۹۷")
        self.assertIsNotNone(m)
        self.assertEqual(m.path, "pride/131")

    def test_persian_match_peugeot(self):
        self.assertEqual(self.cat.match_in("پژو ۲۰۶ تیپ ۲").path, "peugeot/206")

    def test_persian_match_dena(self):
        self.assertEqual(self.cat.match_in("دنا پلاس ۵ دنده").path, "dena/plus")

    def test_no_match_garbage(self):
        self.assertIsNone(self.cat.match_in("آگهی تست بی‌ربط"))

    def test_prefers_model_over_brand(self):
        m = self.cat.match_in("پژو ۲۰۷ اتومات")
        self.assertEqual(m.brand_path, "peugeot")
        self.assertNotEqual(m.path, "peugeot/206/2")

    def test_children(self):
        models = self.cat.children_of("peugeot")
        self.assertGreater(len(models), 3)
        self.assertTrue(all(m.path.startswith("peugeot/") for m in models))

    def test_key_normalisation(self):
        self.assertEqual(cat.key("پژو ۲۰۶"), cat.key("Peugeot 206").replace("peugeot", "پژو"))
        self.assertEqual(cat.key("۱۳۹۸"), "1398")

    def test_extra_brands_present(self):
        for path in ("sahand", "sorous", "shahin"):
            with self.subTest(path=path):
                self.assertIsNotNone(self.cat.resolve_path(path))

    def test_roundtrip_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.json"
            self.cat.save(path)
            again = cat.load_catalog(path)
            self.assertEqual(len(again), len(self.cat))
            self.assertEqual(again.resolve_path("pride/131").path, "pride/131")


class ModelMatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = load_cat()

    def setUp(self):
        if not len(self.cat):
            self.skipTest("catalog not built")

    def test_match_fields(self):
        from pride_radar import models

        m = models.match_vehicle(self.cat, "پراید ۱۳۱ مدل ۹۷")
        self.assertTrue(models.is_vehicle_listing(m))
        self.assertEqual(m.path, "pride/131")
        self.assertEqual(m.brand_path, "pride")
        self.assertTrue(m.fa)

    def test_trim_reports_parent_model(self):
        from pride_radar import models

        m = models.match_vehicle(self.cat, "پژو ۲۰۶ تیپ ۵ مدل ۹۸")
        self.assertEqual(m.path, "peugeot/206")
        self.assertEqual(m.depth, 2)

    def test_matches_target(self):
        from pride_radar.models import NO_MATCH, matches_target

        m = type("M", (), {"path": "peugeot/206", "brand_path": "peugeot"})()
        self.assertTrue(matches_target(m, ["peugeot/206"]))
        self.assertTrue(matches_target(m, ["peugeot"]))
        self.assertFalse(matches_target(m, ["pride/131"]))
        self.assertTrue(matches_target(m, []))

    def test_year_extraction(self):
        from pride_radar import models

        self.assertEqual(models.extract_year("پراید ۱۳۱ مدل ۹۷"), 1397)
        self.assertEqual(models.extract_year("پراید ۹۰"), 1390)
        self.assertIsNone(models.extract_year("پراید ۱۳۱"))

    def test_mileage_extraction(self):
        from pride_radar import models

        self.assertEqual(models.extract_mileage("کارکرد ۹۰ هزار کیلومتر"), 90_000)
        self.assertEqual(models.extract_mileage("۱۳۱,۰۰۰ کیلومتر"), 131_000)
        self.assertIsNone(models.extract_mileage("بدون کارکرد"))


class ExtractYearTests(unittest.TestCase):
    """A relative timestamp must never be read as a model year."""

    def test_relative_time_is_not_a_year(self):
        from pride_radar.models import extract_year

        # regression: "11 hours ago" used to be coerced into the year 1411
        self.assertIsNone(extract_year("پراید ۱۳۱", "۱۱ ساعت پیش در حصیرآباد", "حصیرآباد"))
        for text in ("۳ روز پیش", "۲ ماه پیش", "۵ دقیقه پیش", "۱ هفته پیش"):
            self.assertIsNone(extract_year(f"پراید ۱۳۱ {text}"), text)

    def test_future_year_is_rejected(self):
        from pride_radar.models import extract_year

        self.assertIsNone(extract_year("پراید ۱۴۱۱"))

    def test_labelled_year_still_works(self):
        from pride_radar.models import extract_year

        self.assertEqual(extract_year("پراید ۱۳۱ مدل ۹۷"), 1397)
        self.assertEqual(extract_year("پراید ۱۳۱ مدل ۱۳۹۹"), 1399)
        self.assertEqual(extract_year("پراید ۱۳۱ سال ۸۵"), 1385)

    def test_mileage_is_not_a_year(self):
        from pride_radar.models import extract_year

        self.assertIsNone(extract_year("پراید ۱۳۱ ۵۰,۰۰۰ کیلومتر"))

    def test_year_range_is_not_a_year(self):
        from pride_radar.models import extract_year

        self.assertIsNone(extract_year("پراید ۱۳۱ ۱۳۹۴ - ۲۰۱۵"))


class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def test_upsert_new_and_dedup(self):
        self.assertTrue(self.db.upsert(make_listing("a1")).is_new)
        self.assertFalse(self.db.upsert(make_listing("a1")).is_new)
        self.assertEqual(len(self.db.query()), 1)

    def test_vehicle_roundtrip(self):
        self.db.upsert(make_listing("a1", vehicle="peugeot/206"))
        got = self.db.get("a1")
        self.assertEqual(got.vehicle_path, "peugeot/206")
        self.assertEqual(got.brand_path, "peugeot")

    def test_price_change_and_history(self):
        self.db.upsert(make_listing("a1", price=250_000_000))
        r = self.db.upsert(make_listing("a1", price=230_000_000))
        self.assertTrue(r.price_changed)
        self.assertEqual([p for _, p in self.db.price_history("a1")], [250_000_000, 230_000_000])

    def test_price_drops(self):
        self.db.upsert(make_listing("a1", price=250_000_000))
        self.db.upsert(make_listing("a1", price=200_000_000))
        self.assertEqual(len(self.db.recent_price_drops()), 1)

    def test_query_by_vehicle_path(self):
        self.db.upsert(make_listing("a1", vehicle="pride/131"))
        self.db.upsert(make_listing("a2", vehicle="peugeot/206"))
        self.assertEqual(len(self.db.query(vehicle_paths=["peugeot/206"])), 1)

    def test_query_by_brand(self):
        self.db.upsert(make_listing("a1", vehicle="pride/131"))
        self.db.upsert(make_listing("a2", vehicle="peugeot/206"))
        self.assertEqual(len(self.db.query(brand_paths=["pride"])), 1)

    def test_query_filters(self):
        self.db.upsert(make_listing("a1", price=100_000_000, year=1390, mileage=300_000))
        self.db.upsert(make_listing("a2", price=300_000_000, year=1399, mileage=50_000))
        self.assertEqual(len(self.db.query(min_year=1395)), 1)
        self.assertEqual(len(self.db.query(max_price_toman=200_000_000)), 1)
        self.assertEqual(len(self.db.query(max_mileage_km=100_000)), 1)

    def test_notification_once(self):
        self.db.upsert(make_listing("a1"))
        self.assertFalse(self.db.already_notified("a1", "r"))
        self.db.mark_notified("a1", "r", 90.0)
        self.assertTrue(self.db.already_notified("a1", "r"))

    def test_purge(self):
        self.db.upsert(make_listing("a1", age_hours=24 * 400))
        self.db.execute(
            "UPDATE listings SET last_seen_at=? WHERE listing_id='a1'",
            ((utcnow() - timedelta(days=400)).isoformat(),),
        )
        self.assertEqual(self.db.purge_older_than(180), 1)

    def test_migration_adds_columns(self):
        columns = {r["name"] for r in self.db.execute("PRAGMA table_info(listings)")}
        for c in ("vehicle_path", "vehicle_name", "vehicle_fa", "brand_path", "brand_fa"):
            self.assertIn(c, columns)


class MarketTests(unittest.TestCase):
    def setUp(self):
        self.listings = [
            make_listing(f"m{i}", price=200_000_000 + i * 10_000_000, year=1398)
            for i in range(5)
        ]
        self.snapshot = build_snapshot(self.listings)

    def test_median(self):
        self.assertEqual(self.snapshot.overall.median, 220_000_000)

    def test_by_vehicle(self):
        self.assertIn("pride/131", self.snapshot.by_vehicle)

    def test_by_brand(self):
        self.assertIn("pride", self.snapshot.by_brand)

    def test_compare_below(self):
        v = compare_to_market(200_000_000, self.snapshot, "pride/131", 1398)
        self.assertTrue(v.is_below_market)

    def test_compare_suspicious(self):
        noisy = self.listings + [make_listing("x", price=1_000)]
        v = compare_to_market(1_000, build_snapshot(noisy), "pride/131", 1398)
        self.assertEqual(v.label, "مشکوک به قیمت غیرواقعی")
        self.assertIsNone(suggest_offer(v))

    def test_implausible_does_not_corrupt_group(self):
        mixed = self.listings + [
            make_listing("lp1", price=290_000_000, vehicle="peugeot/206", year=1397),
            make_listing("lp2", price=279_000_000, vehicle="peugeot/206", year=1398),
            make_listing("lp3", price=3_000_000, vehicle="peugeot/206", year=1396),
        ]
        self.assertEqual(build_snapshot(mixed).by_vehicle["peugeot/206"].median, 284_500_000)

    def test_suggest_offer(self):
        v = compare_to_market(180_000_000, self.snapshot, "pride/131", 1398)
        self.assertEqual(suggest_offer(v), 180_000_000)


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = build_snapshot(
            [make_listing(f"s{i}", price=250_000_000) for i in range(6)]
        )
        self.rule = Rule(
            id="r", name="t", paths=["pride/131"],
            max_price_toman=260_000_000, exclude_business=True,
            max_discount_vs_market=0.1,
        )

    def test_hit(self):
        v, r = check_rule(make_listing("x", price=220_000_000), self.rule, self.snapshot)
        self.assertIsNotNone(v)
        self.assertIsNone(r)

    def test_reject_other_vehicle(self):
        _, r = check_rule(make_listing("x", vehicle="peugeot/206"), self.rule, self.snapshot)
        self.assertIsNotNone(r)

    def test_reject_price(self):
        _, r = check_rule(make_listing("x", price=300_000_000), self.rule, self.snapshot)
        self.assertIsNotNone(r)

    def test_reject_business(self):
        _, r = check_rule(
            make_listing("x", price=220_000_000, business=True), self.rule, self.snapshot
        )
        self.assertEqual(r.reason, "آگهی مغازه‌ای")

    def test_reject_missing_price(self):
        _, r = check_rule(make_listing("x", price=None), self.rule, self.snapshot)
        self.assertEqual(r.reason, "قیمت مشخص نیست")

    def test_evaluate(self):
        res = evaluate(make_listing("x", price=220_000_000), [self.rule], self.snapshot, WEIGHTS)
        self.assertEqual(len(res.matched), 1)

    def test_score_range(self):
        s = score_listing(make_listing("x"), self.snapshot, WEIGHTS)
        self.assertGreaterEqual(s, 0)
        self.assertLessEqual(s, 100)

    def test_global_filters(self):
        f = FilterConfig(min_year=1395, exclude_business=True)
        self.assertIsNotNone(passes_global_filters(make_listing(year=1390), f))
        self.assertIsNone(passes_global_filters(make_listing(year=1398), f))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        example = ROOT / "config.example.json"
        self.path.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_loads_example(self):
        c = load_config(self.path)
        self.assertEqual(c.search.city, "ahvaz")
        self.assertEqual(c.search.city_id, "7")
        self.assertGreaterEqual(len(c.targets), 1)
        self.assertEqual(len(c.rules), 3)

    def test_target_from_string(self):
        self.path.write_text('{"targets": ["پژو ۲۰۶"]}', encoding="utf-8")
        c = load_config(self.path)
        self.assertEqual(len(c.targets), 1)
        self.assertEqual(c.targets[0].query, "پژو ۲۰۶")

    def test_target_from_paths(self):
        self.path.write_text(
            '{"targets": [{"id":"p206","paths":["peugeot/206"]}]}', encoding="utf-8"
        )
        c = load_config(self.path)
        self.assertEqual(c.targets[0].paths, ["peugeot/206"])

    def test_bare_path_becomes_brand(self):
        self.path.write_text('{"targets": [{"id":"x","paths":["pride"]}]}', encoding="utf-8")
        c = load_config(self.path)
        self.assertIn("pride", c.targets[0].brand_paths)

    def test_duplicate_target_id_rejected(self):
        self.path.write_text(
            '{"targets": [{"id":"a"},{"id":"a"}]}', encoding="utf-8"
        )
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_rule_unknown_target_rejected(self):
        self.path.write_text(
            '{"targets": [{"id":"a"}], "rules": [{"id":"r","name":"r","target_ids":["zzz"]}]}',
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_missing_city_id_rejected(self):
        self.path.write_text('{"search": {"city": "ahvaz", "city_id": ""}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_bad_weights_rejected(self):
        self.path.write_text('{"scoring": {"weights": {"price": 0.9}}}', encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(self.path)

    def test_paths_resolve(self):
        c = load_config(self.path)
        self.assertEqual(c.db_path, Path(self.tmp.name) / "data" / "pride.db")

    def test_active_targets(self):
        self.path.write_text(
            '{"targets": [{"id":"a"},{"id":"b","enabled":false}]}', encoding="utf-8"
        )
        c = load_config(self.path)
        self.assertEqual([t.id for t in c.active_targets], ["a"])


class ClientBodyTests(unittest.TestCase):
    def test_brand_model_shape(self):
        from pride_radar.client import build_body

        body = build_body(city_id="7", brand_model=["Pride 131"])
        bm = body["search_data"]["form_data"]["data"]["brand_model"]
        self.assertEqual(bm, {"repeated_string": {"value": ["Pride 131"]}})

    def test_no_brand_model_key_when_absent(self):
        from pride_radar.client import build_body

        body = build_body(city_id="7")
        self.assertNotIn("brand_model", body["search_data"]["form_data"]["data"])

    def test_query_and_pagination(self):
        from pride_radar.client import build_body

        body = build_body(query="پراید", page=2, pagination={"viewed_tokens": "x"})
        self.assertEqual(body["search_data"]["query"], "پراید")
        self.assertEqual(body["page"], 2)
        self.assertEqual(body["pagination_data"], {"viewed_tokens": "x"})

    def test_city_ids(self):
        from pride_radar.client import build_body

        body = build_body(city_id="7")
        self.assertEqual(body["city_ids"], ["7"])
        self.assertEqual(body["user_selected_location"]["places"][0]["place_id"], "7")


class TargetScopeTests(unittest.TestCase):
    """The v8 backend ignores brand_model, so we must scope results ourselves."""

    def setUp(self):
        self.catalog = load_cat()

    def test_model_target_does_not_add_parent_brand(self):
        from pride_radar.pipeline import target_terms

        terms = target_terms(self.catalog, VehicleTarget(id="t", paths=["pride/131"]))
        self.assertEqual(terms, ["Pride 131"])
        self.assertNotIn("Pride", terms)

    def test_brand_target_sends_brand(self):
        from pride_radar.pipeline import target_terms

        terms = target_terms(self.catalog, VehicleTarget(id="t", brand_paths=["pride"]))
        self.assertEqual(terms, ["Pride"])

    def test_model_target_keeps_only_exact_path(self):
        from pride_radar.pipeline import listing_in_target

        target = VehicleTarget(id="t", paths=["pride/131"])
        self.assertTrue(listing_in_target(make_listing(vehicle="pride/131"), target))
        # trims below the model path are part of the target
        self.assertTrue(listing_in_target(make_listing(vehicle="pride/131/x"), target))
        # siblings, the parent brand and other brands are not
        self.assertFalse(listing_in_target(make_listing(vehicle="pride/111"), target))
        self.assertFalse(listing_in_target(make_listing(vehicle="pride/pickup"), target))
        self.assertFalse(listing_in_target(make_listing(vehicle="pride"), target))
        self.assertFalse(listing_in_target(make_listing(vehicle="peugeot/206"), target))

    def test_brand_target_accepts_whole_brand(self):
        from pride_radar.pipeline import listing_in_target

        target = VehicleTarget(id="t", brand_paths=["pride"])
        self.assertTrue(listing_in_target(make_listing(vehicle="pride/131"), target))
        self.assertTrue(listing_in_target(make_listing(vehicle="pride/111"), target))
        self.assertFalse(listing_in_target(make_listing(vehicle="peugeot/206"), target))

    def test_scope_listings_reports_dropped(self):
        from pride_radar.pipeline import scope_listings

        target = VehicleTarget(id="t", paths=["pride/131"])
        rows = [
            make_listing("a", vehicle="pride/131"),
            make_listing("b", vehicle="pride/111"),
            make_listing("c", vehicle="pride/131"),
            make_listing("d", vehicle="peugeot/206"),
        ]
        kept, dropped = scope_listings(rows, target)
        self.assertEqual([x.listing_id for x in kept], ["a", "c"])
        self.assertEqual(dropped, 2)

    def test_all_target_keeps_everything(self):
        from pride_radar.pipeline import scope_listings

        target = VehicleTarget(id="t")
        rows = [make_listing("a", vehicle="pride/131"), make_listing("b", vehicle="dena")]
        kept, dropped = scope_listings(rows, target)
        self.assertEqual(len(kept), 2)
        self.assertEqual(dropped, 0)


class FlipScoreTests(unittest.TestCase):
    """Body condition lives in body_condition; paint is only the colour."""

    def setUp(self):
        rows = [
            make_listing(f"m{i}", price=300_000_000, vehicle="pride/131", year=1398)
            for i in range(9)
        ]
        rows.append(make_listing("cheap", price=250_000_000, vehicle="pride/131", year=1398))
        self.snapshot = build_snapshot(rows)

    def _score(self, **kw):
        from pride_radar.market import flip_score

        return flip_score(make_listing("x", vehicle="pride/131", **kw), self.snapshot)

    def test_white_clean_car_is_not_flagged_as_painted(self):
        s = self._score(paint="سفید", body_condition="بی‌خط و خش", chassis="سالم")
        self.assertEqual(s.flags, ())
        self.assertEqual(s.condition, 1.0)

    def test_painted_body_penalised(self):
        clean = self._score(paint="سفید", body_condition="بی‌خط و خش", chassis="سالم")
        painted = self._score(paint="سفید", body_condition="رنگ‌شده", chassis="سالم")
        self.assertLess(painted.condition, clean.condition)
        self.assertIn("بدنه رنگ‌شده", painted.flags)

    def test_bad_chassis_penalised_and_flagged(self):
        bad = self._score(body_condition="بی‌خط و خش", chassis="آسیب‌دارد")
        self.assertIn("شاسی مشکل‌دار", bad.flags)
        self.assertLess(bad.condition, 1.0)

    def test_business_flagged(self):
        s = self._score(body_condition="بی‌خط و خش", chassis="سالم", business=True)
        self.assertIn("مغازه‌ای", s.flags)


class BodyTypeTests(unittest.TestCase):
    def test_body_type_from_catalog_path_not_global_scan(self):
        from pride_radar.specs import body_for_path

        # the 131 slug carries no body word, so it stays unknown
        self.assertIsNone(body_for_path("pride/131"))
        self.assertEqual(body_for_path("pride/131/ex"), None)
        self.assertEqual(body_for_path("chevrolet/pickup"), "وانت")
        self.assertEqual(body_for_path("brilliance/van"), "وان")
        self.assertEqual(body_for_path("baic/sabrina/hatchback"), "هاچبک")
        self.assertEqual(body_for_path("bmw/3-series-sedan"), "سدان")
        self.assertEqual(body_for_path("jeep/wagoneer"), "استیشن")

    def test_find_body_returns_nothing(self):
        from pride_radar.specs import _find_body

        catalog = load_cat()
        if len(catalog):
            self.assertIsNone(_find_body(catalog))

    def test_parse_post_row_scopes_body_type_per_listing(self):
        from pride_radar.parse import parse_post_row

        def row(title):
            return parse_post_row(
                {
                    "widget_type": "POST_ROW",
                    "data": {
                        "title": title,
                        "top_description_text": "۵۰,۰۰۰ کیلومتر",
                        "middle_description_text": "۳۱۰,۰۰۰,۰۰۰ تومان",
                        "bottom_description_text": "۱ ساعت پیش",
                        "token": "tok1",
                        "action": {"payload": {"web_info": {"city_persian": "اهواز"}}},
                    },
                },
                load_cat(),
            )

        sedan = row("پژو ۲۰۶ تیپ ۲ صندوقدار")
        pickup = row("وانت پراید ۱۳۱")
        self.assertIsNotNone(sedan)
        self.assertIsNotNone(pickup)
        # a sedan must never inherit the pickup's body style
        self.assertNotEqual(sedan.body_type, "وانت")
        self.assertNotEqual(pickup.body_type, sedan.body_type)


if __name__ == "__main__":
    unittest.main()
