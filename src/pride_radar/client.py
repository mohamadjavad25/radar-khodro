"""Divar v8 search client (verified working schema).

Endpoint: POST https://api.divar.ir/v8/postlist/w/search
Page URL: https://divar.ir/s/{city_slug}/{category}?q={query}

Requests run inside a real browser context so TLS fingerprint, cookies and
headers match the web app. Direct urllib calls are rejected with 403.
"""

from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import catalog as catalog_mod
from .models import Listing
from .parse import breadcrumb, parse_pagination, parse_search_payload, result_count

API_HOST = "api.divar.ir"
SEARCH_PATH = "/v8/postlist/w/search"
WEB_ORIGIN = "https://divar.ir"
CATALOG_FILE = Path(__file__).resolve().parents[2] / "data" / "catalog-light.json"
FILTERS_FILE = Path(__file__).resolve().parents[2] / "data" / "divar-filters.json"

SERVER_PAYLOAD_TYPE = "type.googleapis.com/widgets.SearchData.ServerPayload"

CATEGORY_IDS = {
    "light": "light",
    "car": "light",
    "cars": "light",
    "heavy": "heavy",
    "motorcycle": "motor",
    "auto": "light",
}

SORT_NEWEST = "sort_date"
SORT_PRICE_ASC = "sort_price_asc"
SORT_PRICE_DESC = "sort_price_desc"


class ClientError(RuntimeError):
    pass


def build_body(
    *,
    query: str = "",
    category: str = "light",
    city_id: str = "7",
    page: int = 1,
    sort: str = SORT_NEWEST,
    pagination: dict[str, Any] | None = None,
    brand_model: list[str] | None = None,
) -> dict[str, Any]:
    """Build the v8 search request body.

    ``brand_model`` takes Divar *display names* (e.g. ["Pride 131", "Peugeot 206"])
    as produced by the catalog, not URL slugs.
    """
    data: dict[str, Any] = {"category": {"str": {"value": CATEGORY_IDS.get(category, category)}}}
    if brand_model:
        data["brand_model"] = {"repeated_string": {"value": list(brand_model)}}

    search_data: dict[str, Any] = {
        "form_data": {"data": data},
        "server_payload": {
            "@type": SERVER_PAYLOAD_TYPE,
            "additional_form_data": {"data": {"sort": {"str": {"value": sort}}}},
        },
    }
    if query:
        search_data["query"] = query

    body: dict[str, Any] = {
        "disable_recommendation": False,
        "map_state": {"camera_info": {"bbox": {}}},
        "search_data": search_data,
        "city_ids": [str(city_id)],
        "user_selected_location": {"places": [{"place_id": str(city_id)}]},
    }
    if pagination:
        body["pagination_data"] = pagination
    if page > 1:
        body["page"] = page
    return body


def page_url(
    city_slug: str,
    category: str = "car",
    query: str = "",
    brand_model: list[str] | None = None,
) -> str:
    from urllib.parse import quote

    url = f"{WEB_ORIGIN}/s/{city_slug}/{category}"
    if brand_model:
        url += "/" + "/".join(quote(part) for part in brand_model)
    if query:
        url += f"?q={quote(query)}"
    return url


@dataclass(slots=True)
class SearchPage:
    listings: list[Listing]
    pagination: dict[str, Any] | None
    total_rows: int
    breadcrumb: list[str] = field(default_factory=list)


def _load_catalog(category: str = "light") -> Any:
    cached = catalog_mod.load_catalog(CATALOG_FILE)
    if len(cached):
        return cached
    return catalog_mod.load_from_filters_file(str(FILTERS_FILE), category)


class DivarClient:
    """Browser-backed v8 search client."""

    def __init__(
        self, headless: bool = True, timeout_ms: int = 45_000, category: str = "light"
    ) -> None:
        self.headless = headless
        self.timeout_ms = timeout_ms
        self.category = category
        self.catalog = _load_catalog(category)
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._page: Any = None

    def __enter__(self) -> DivarClient:
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise ClientError(
                "برای دریافت آگهی به playwright نیاز است. نصب: pip install playwright"
            ) from exc

        self._playwright = sync_playwright().start()
        errors: list[str] = []
        for channel in ("chrome", "msedge", "chromium"):
            try:
                self._browser = self._playwright.chromium.launch(
                    channel=None if channel == "chromium" else channel,
                    headless=self.headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-default-browser-check",
                    ],
                )
                break
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{channel}: {type(exc).__name__}")
        if self._browser is None:
            self._playwright.stop()
            self._playwright = None
            raise ClientError("مرورگری در دسترس نیست: " + ", ".join(errors))

        self._context = self._browser.new_context(
            locale="fa-IR",
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
        )
        self._page = self._context.new_page()
        self._warmup()

    def _warmup(self) -> None:
        try:
            self._page.goto(
                f"{WEB_ORIGIN}/s/tehran", wait_until="domcontentloaded", timeout=self.timeout_ms
            )
            self._page.wait_for_timeout(2500)
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        for attr in ("_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass
                setattr(self, attr, None)
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                pass
            self._playwright = None
        self._page = None

    def _post(self, body: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
        if self._page is None:
            raise ClientError("کلاینت شروع نشده است")

        script = """
        async ([url, body]) => {
            const r = await fetch(url, {
                method: 'POST',
                credentials: 'include',
                headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                body: JSON.stringify(body),
            });
            return {status: r.status, text: await r.text()};
        }
        """
        url = f"https://{API_HOST}{SEARCH_PATH}"
        last: str = ""

        for attempt in range(attempts):
            result = self._page.evaluate(script, [url, body])
            status = result.get("status", 0)
            text = result.get("text", "")
            if status == 200:
                try:
                    return json.loads(text)
                except ValueError as exc:
                    raise ClientError("پاسخ JSON معتبر نبود") from exc
            last = f"HTTP {status}: {text[:160]}"
            if status in (401, 403):
                raise ClientError(f"دیوار درخواست را رد کرد ({status}). احتمالاً محدودیت ضدربات.")
            time.sleep(random.uniform(2.5, 5.0) * (attempt + 1))

        raise ClientError(f"درخواست ناموفق بود - {last}")

    def search(
        self,
        *,
        query: str = "",
        category: str = "light",
        city_id: str = "7",
        page: int = 1,
        sort: str = SORT_NEWEST,
        pagination: dict[str, Any] | None = None,
        brand_model: list[str] | None = None,
    ) -> SearchPage:
        body = build_body(
            query=query,
            category=category,
            city_id=city_id,
            page=page,
            sort=sort,
            pagination=pagination,
            brand_model=brand_model,
        )
        payload = self._post(body)
        return SearchPage(
            listings=parse_search_payload(payload, self.catalog),
            pagination=parse_pagination(payload),
            total_rows=result_count(payload),
            breadcrumb=breadcrumb(payload),
        )

    def health(self) -> bool:
        try:
            page = self.search(page=1)
        except ClientError:
            return False
        return bool(page.listings)

    def fetch_specs(self, token: str, timeout_ms: int | None = None) -> Any:
        """Open a post page and read its car specs (paint, chassis, gearbox, …)."""
        from . import specs as specs_mod

        if self._page is None:
            raise ClientError("کلاینت شروع نشده است")
        url = f"{WEB_ORIGIN}/v/{token}"
        limit = timeout_ms or self.timeout_ms
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=limit)
            self._page.wait_for_timeout(1800)
            text = self._page.evaluate("() => document.body.innerText") or ""
            title = self._page.title() or ""
        except Exception as exc:  # noqa: BLE001
            raise ClientError(f"جزئیات آگهی {token} باز نشد: {type(exc).__name__}") from exc

        if "نمی" in text[:400] and "رسد" in text[:400]:
            raise ClientError("آگهی حذف شده است")

        if not text.strip():
            # The post page rendered nothing at all. Say so, so the run log shows
            # why a car has no specs instead of silently storing an empty row.
            raise ClientError(f"صفحهٔ آگهی {token} خالی رندر شد")

        result = specs_mod.parse_specs(text, catalog=self.catalog)
        return result

    def refresh_catalog(self) -> int:
        """Rebuild the catalog from Divar's filter config and cache it."""
        cat = catalog_mod.load_from_filters_file(str(FILTERS_FILE), self.category)
        if len(cat):
            cat.save(CATALOG_FILE)
            self.catalog = cat
        return len(self.catalog)
