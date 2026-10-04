"""Turn real Divar v8 search responses into Listing objects.

Verified response shape (POST api.divar.ir/v8/postlist/w/search):

    {"list_widgets": [{"widget_type": "POST_ROW", "data": {
        "title": "پراید ۱۳۱مدل۸۴",
        "top_description_text": "۵۰,۰۰۰ کیلومتر",       <- mileage
        "middle_description_text": "۳۱۰,۰۰۰,۰۰۰ تومان",  <- price
        "bottom_description_text": "۱ ساعت پیش در کوی علوی",
        "red_text": "پله شده",
        "token": "ga8azi_1",
        "action": {"payload": {"web_info": {
            "city_persian": "اهواز", "district_persian": "کوی علوی"}}},
    }, "action_log": {"server_side_info": {"info": {
        "sort_date": "2026-09-29T22:07:18.868155Z", "post_type": "SEARCH"}}}}]}
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import datetime, timezone
from typing import Any

from . import models
from . import text as text_utils
from .models import Listing, Price

DETAIL_URL = "https://divar.ir/v/{token}"

_ISO_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T[\d:.]+Z?)")
_BIZINESS_HINTS = ("مغازه", "عمده", "نمایشگاه", "شرکت", "فروش عمده", "دست دوم عمده")
_ROW_WIDGETS = ("POST_ROW", "post_row")


def _iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    match = _ISO_RE.search(str(value))
    if not match:
        return None
    raw = match.group(1).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _web_info(data: dict[str, Any]) -> dict[str, Any]:
    action = data.get("action")
    if not isinstance(action, dict):
        return {}
    payload = action.get("payload")
    if not isinstance(payload, dict):
        return {}
    info = payload.get("web_info")
    return info if isinstance(info, dict) else {}


def _action_log(widget: dict[str, Any]) -> dict[str, Any]:
    log = widget.get("action_log")
    if not isinstance(log, dict):
        return {}
    server = log.get("server_side_info")
    if not isinstance(server, dict):
        return {}
    info = server.get("info")
    return info if isinstance(info, dict) else {}


def is_business_listing(data: dict[str, Any], title: str, district: str | None) -> bool:
    tag = (data.get("image_top_left_tag") or {}).get("text")
    if tag and "عمده" in str(tag):
        return True
    red = data.get("red_text")
    if red and any(hint in str(red) for hint in _BIZINESS_HINTS):
        return True
    haystack = f"{title} {district or ''}"
    return any(hint in haystack for hint in ("مغازه", "عمده فروش"))


def parse_mileage_text(text: str) -> int | None:
    if "کیلومتر" not in text:
        return None
    return models.extract_mileage(text)


def parse_price_text(text: str) -> tuple[int | None, bool]:
    if not text:
        return None, False
    if "تومان" in text or "ریال" in text or text_utils.is_negotiable(text):
        return text_utils.parse_price(text, min_toman=1)
    return None, text_utils.is_negotiable(text)


def parse_post_row(widget: dict[str, Any], catalog: Any = None) -> Listing | None:
    data = widget.get("data")
    if not isinstance(data, dict):
        return None

    token = data.get("token")
    if not token:
        return None

    title = str(data.get("title") or "").strip()
    info = _web_info(data)
    district = info.get("district_persian")
    city = info.get("city_persian")

    top = str(data.get("top_description_text") or "").strip()
    middle = str(data.get("middle_description_text") or "").strip()
    bottom = str(data.get("bottom_description_text") or "").strip()

    sources = (title, bottom, district or "")

    match = models.match_vehicle(catalog, *sources) if catalog is not None else models.NO_MATCH
    if not models.is_vehicle_listing(match):
        return None

    mileage = parse_mileage_text(top) or models.extract_mileage(*sources)
    price_toman, negotiable = parse_price_text(middle)

    log = _action_log(widget)
    created = _iso_datetime(log.get("sort_date")) or _iso_datetime(data.get("created_at"))
    year = _explicit_year(data) or models.extract_year(*sources)

    # The list's bottom line already carries the relative publish time.
    from . import specs as specs_mod

    published_at, published_text = specs_mod.parse_publish_time(bottom)
    body_type = specs_mod.body_for_path(match.path)

    return Listing(
        listing_id=str(token),
        title=title,
        description=bottom,
        price=Price(toman=price_toman, negotiable=negotiable, raw=middle or top),
        vehicle_path=match.path,
        vehicle_name=match.name,
        vehicle_fa=match.fa,
        brand_path=match.brand_path,
        brand_fa=match.brand_fa,
        year=year,
        mileage_km=mileage,
        city=str(city) if city else None,
        district=str(district) if district else None,
        url=DETAIL_URL.format(token=token),
        business=is_business_listing(data, title, district if district else None),
        published_at=published_at,
        published_text=published_text,
        body_type=body_type,
        created_at=created,
        raw=data,
    )


def _explicit_year(data: dict[str, Any]) -> int | None:
    """Read a structured year field when Divar supplies one."""
    for key in ("year", "car_year", "production_year"):
        value = data.get(key)
        if isinstance(value, int) and 1300 <= value <= 1450:
            return value
        if isinstance(value, str) and value.isdigit():
            year = int(value)
            if 1300 <= year <= 1450:
                return year
    vehicle = data.get("vehicle")
    if isinstance(vehicle, dict):
        return _explicit_year(vehicle)
    return None


def apply_specs(listing: Listing, car_specs: Any) -> Listing:
    """Merge scraped car specs into a listing."""
    from . import specs as specs_mod

    if car_specs is None:
        return listing
    listing.paint = car_specs.paint
    listing.chassis = car_specs.chassis
    listing.body_condition = car_specs.body
    listing.engine = car_specs.engine
    listing.gearbox = car_specs.gearbox
    listing.gearbox_state = car_specs.gearbox_state
    listing.fuel = car_specs.fuel
    listing.insurance = car_specs.insurance
    listing.trim = car_specs.trim
    listing.body_type = specs_mod.body_for_path(listing.vehicle_path) or car_specs.body_type
    if car_specs.published_at:
        listing.published_at = car_specs.published_at
        listing.published_text = car_specs.published_text
    listing.specs_fetched = True
    return listing


def iter_post_rows(payload: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    if depth > 10:
        return
    if isinstance(payload, list):
        for item in payload:
            yield from iter_post_rows(item, depth + 1)
        return
    if not isinstance(payload, dict):
        return

    if payload.get("widget_type") in _ROW_WIDGETS:
        yield payload
        return

    widgets = payload.get("list_widgets")
    if isinstance(widgets, list):
        yield from iter_post_rows(widgets, depth + 1)


def parse_search_payload(payload: Any, catalog: Any = None) -> list[Listing]:
    listings: list[Listing] = []
    seen: set[str] = set()
    for widget in iter_post_rows(payload):
        listing = parse_post_row(widget, catalog)
        if listing and listing.listing_id not in seen:
            seen.add(listing.listing_id)
            listings.append(listing)
    return listings


def parse_pagination(payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    pagination = payload.get("pagination")
    if not isinstance(pagination, dict):
        return None
    if pagination.get("has_next_page") is False:
        return None
    data = pagination.get("data")
    return data if isinstance(data, dict) else None


def breadcrumb(payload: Any) -> list[str]:
    if not isinstance(payload, dict):
        return []
    seo = payload.get("seo_details")
    if not isinstance(seo, dict):
        return []
    crumbs = seo.get("bread_crumb")
    if not isinstance(crumbs, list):
        return []
    return [str(item.get("name")) for item in crumbs if isinstance(item, dict) and item.get("name")]


def result_count(payload: Any) -> int:
    if not isinstance(payload, dict):
        return 0
    return len(list(iter_post_rows(payload)))
