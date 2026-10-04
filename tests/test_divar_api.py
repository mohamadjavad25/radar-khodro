import json
import unittest
from pathlib import Path

from pride_radar.client import build_body, page_url
from pride_radar.parse import (
    breadcrumb,
    parse_mileage_text,
    parse_post_row,
    parse_price_text,
    parse_search_payload,
    result_count,
)

ROOT = Path(__file__).resolve().parents[1]
FILTERS = ROOT / "data" / "divar-filters.json"


def build_catalog():
    from pride_radar import catalog as cat

    if not FILTERS.is_file():
        return cat.Catalog()
    return cat.build_from_filters(json.loads(FILTERS.read_text(encoding="utf-8")))


def row_widget(
    token="t1",
    title="پژو ۲۰۶ مدل ۹۸",
    top="۹۰,۰۰۰ کیلومتر",
    middle="۳۱۰,۰۰۰,۰۰۰ تومان",
    bottom="۱ ساعت پیش در کوی علوی",
    city="اهواز",
    district="کوی علوی",
    biz_tag=None,
):
    data = {
        "title": title,
        "top_description_text": top,
        "middle_description_text": middle,
        "bottom_description_text": bottom,
        "token": token,
        "layout_type": "GRID_ITEM",
        "action": {
            "type": "VIEW_POST",
            "payload": {
                "web_info": {"city_persian": city, "district_persian": district}
            },
        },
    }
    if biz_tag:
        data["image_top_left_tag"] = {"text": biz_tag}
    return {
        "widget_type": "POST_ROW",
        "data": data,
        "action_log": {
            "server_side_info": {
                "info": {
                    "sort_date": "2026-09-29T22:07:18.868155Z",
                    "post_type": "SEARCH",
                }
            }
        },
    }


def payload(*widgets, has_next=True):
    return {
        "list_widgets": list(widgets),
        "pagination": {"has_next_page": has_next, "data": {"page": 1}},
        "seo_details": {"bread_crumb": [{"name": "خودرو"}, {"name": "اهواز"}]},
    }


class FieldMappingTests(unittest.TestCase):
    def test_mileage_from_top_description(self):
        self.assertEqual(parse_mileage_text("۹۰,۰۰۰ کیلومتر"), 90_000)
        self.assertEqual(parse_mileage_text("۱۲۴ هزار کیلومتر"), 124_000)
        self.assertEqual(parse_mileage_text("۹۸,۵۲۰ کیلومتر"), 98_520)
        self.assertIsNone(parse_mileage_text(""))

    def test_price_from_middle_description(self):
        self.assertEqual(parse_price_text("۳۱۰,۰۰۰,۰۰۰ تومان")[0], 310_000_000)
        self.assertEqual(parse_price_text("۵۸,۵۰۰,۰۰۰ تومان")[0], 58_500_000)
        self.assertEqual(parse_price_text("۹,۸۰۰,۰۰۰,۰۰۰ تومان")[0], 9_800_000_000)
        self.assertEqual(parse_price_text("۲۵۰ میلیون تومان")[0], 250_000_000)

    def test_price_negotiable(self):
        self.assertEqual(parse_price_text("قابل توافق"), (None, True))
        self.assertEqual(parse_price_text("تماس بگیرید"), (None, True))
        self.assertEqual(parse_price_text(""), (None, False))

    def test_fake_prices_still_parsed(self):
        for raw in ("۱,۰۰۰ تومان", "۶۲۰,۰۰۰ تومان"):
            self.assertIsNotNone(parse_price_text(raw)[0])


class PostRowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = build_catalog()

    def setUp(self):
        if not len(self.cat):
            self.skipTest("catalog not built")

    def test_parses_all_fields(self):
        item = parse_post_row(row_widget(), self.cat)
        self.assertIsNotNone(item)
        self.assertEqual(item.listing_id, "t1")
        self.assertEqual(item.vehicle_path, "peugeot/206")
        self.assertEqual(item.year, 1398)
        self.assertEqual(item.mileage_km, 90_000)
        self.assertEqual(item.price.toman, 310_000_000)
        self.assertEqual(item.city, "اهواز")
        self.assertEqual(item.district, "کوی علوی")
        self.assertEqual(item.url, "https://divar.ir/v/t1")
        self.assertFalse(item.business)

    def test_pride(self):
        item = parse_post_row(row_widget(title="پراید ۱۳۱ مدل ۹۷"), self.cat)
        self.assertEqual(item.vehicle_path, "pride/131")

    def test_dena(self):
        item = parse_post_row(row_widget(title="دنا پلاس ۱۴۰۲"), self.cat)
        self.assertEqual(item.vehicle_path, "dena/plus")

    def test_trim_keeps_model(self):
        item = parse_post_row(row_widget(title="پژو ۲۰۶ تیپ ۵"), self.cat)
        self.assertEqual(item.vehicle_path, "peugeot/206")

    def test_business_tag(self):
        item = parse_post_row(
            row_widget(title="فروش پژو ۲۰۶ عمده", biz_tag="عمده"), self.cat
        )
        self.assertTrue(item.business)

    def test_non_vehicle_rejected(self):
        self.assertIsNone(parse_post_row(row_widget(title="آگهی تست بی‌ربط"), self.cat))

    def test_missing_token(self):
        widget = row_widget()
        widget["data"].pop("token")
        self.assertIsNone(parse_post_row(widget, self.cat))

    def test_label_present(self):
        item = parse_post_row(row_widget(), self.cat)
        self.assertTrue(item.label)
        self.assertTrue(item.vehicle_fa)


class SearchPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = build_catalog()

    def setUp(self):
        if not len(self.cat):
            self.skipTest("catalog not built")

    def test_dedup(self):
        self.assertEqual(len(parse_search_payload(payload(row_widget(), row_widget()), self.cat)), 1)

    def test_mixed(self):
        result = parse_search_payload(
            payload(
                row_widget("a", title="پژو ۲۰۶"),
                row_widget("b", title="آگهی بی‌ربط"),
                row_widget("c", title="پراید ۱۳۱"),
            ),
            self.cat,
        )
        self.assertEqual([i.listing_id for i in result], ["a", "c"])

    def test_result_count(self):
        self.assertEqual(result_count(payload(row_widget("a"), row_widget("b"))), 2)

    def test_breadcrumb(self):
        self.assertEqual(breadcrumb(payload()), ["خودرو", "اهواز"])

    def test_empty(self):
        self.assertEqual(parse_search_payload({}, self.cat), [])


class RealTitlesTests(unittest.TestCase):
    """Titles captured verbatim from live Divar Ahvaz searches."""

    CASES = [
        ("پراید ۱۳۱ مدل ۹۷", "pride/131", 1397),
        ("پراید 131se", "pride/131", None),
        ("پژو ۲۰۶ تیپ ۲", "peugeot/206", None),
        ("دنا پلاس ۵ دنده", "dena/plus", None),
        ("سمند LX مدل ۹۹", "samand/lx", 1399),
        ("تیبا دوگانه ۹۰", "tiba", 1390),
        ("پژو پارس ۱۴۰۲", "peugeot/pars", 1402),
        ("کوییک اتومات R", "quick", None),
        ("شاهین مدل ۱۴۰۱", "shahin", 1401),
        ("سهاند پلاس", "sahand/plus", None),
        ("رنو تندر ۹۱", "renault/tondar-90", 1391),
        ("وانت پراید ۱۵۱", "pride/pickup", None),
        ("پراید صبا", "pride/saba-glxi", None),
        ("پژو ۲۰۷ اتومات", "peugeot/207i", None),
        ("نیسان تیتانیوم ۹۸", "nissan/titanium", 1398),
        ("ماشین پراید 96", "pride", 1396),
        ("مزدا ۳ نیو ۱۴۰۱", "mazda/3", 1401),
        ("کیا اپتیما ۱۴۰۲", "kia/optima", 1402),
    ]

    @classmethod
    def setUpClass(cls):
        cls.cat = build_catalog()

    def setUp(self):
        if not len(self.cat):
            self.skipTest("catalog not built")

    def test_vehicle_and_year(self):
        from pride_radar.models import extract_year

        for title, expected_path, expected_year in self.CASES:
            with self.subTest(title=title):
                item = parse_post_row(
                    row_widget(token=title[:12], title=title), self.cat
                )
                self.assertIsNotNone(item, f"rejected: {title}")
                self.assertEqual(item.vehicle_path, expected_path)
                if expected_year:
                    self.assertEqual(extract_year(title), expected_year)

    def test_brand_path_consistent(self):
        for title, expected_path, _ in self.CASES:
            with self.subTest(title=title):
                item = parse_post_row(row_widget(token=title[:12], title=title), self.cat)
                self.assertEqual(
                    item.brand_path, expected_path.split("/")[0]
                )


class ClientShapeTests(unittest.TestCase):
    def test_brand_model_repeated_string(self):
        body = build_body(city_id="7", brand_model=["Pride 131", "Peugeot 206"])
        self.assertEqual(
            body["search_data"]["form_data"]["data"]["brand_model"],
            {"repeated_string": {"value": ["Pride 131", "Peugeot 206"]}},
        )

    def test_category_str(self):
        body = build_body(city_id="7", category="light")
        self.assertEqual(
            body["search_data"]["form_data"]["data"]["category"], {"str": {"value": "light"}}
        )

    def test_server_payload_type(self):
        body = build_body(city_id="7")
        self.assertEqual(
            body["search_data"]["server_payload"]["@type"],
            "type.googleapis.com/widgets.SearchData.ServerPayload",
        )

    def test_page_url_with_brand(self):
        url = page_url("ahvaz", "car", "", ["pride", "131"])
        self.assertEqual(url, "https://divar.ir/s/ahvaz/car/pride/131")


if __name__ == "__main__":
    unittest.main()
