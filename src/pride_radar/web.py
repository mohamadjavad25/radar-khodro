"""Local web UI: HTTP server, JSON API and the single-page frontend."""

from __future__ import annotations

import json
import threading
import time
import traceback
import webbrowser
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from . import catalog as catalog_mod
from . import text as text_utils
from .client import ClientError, DivarClient, page_url
from .config import Config, VehicleTarget, load_config
from .db import Database
from .market import SUSPICIOUS_LABEL, build_snapshot, compare_to_market, suggest_offer
from .models import Listing, utcnow
from .notify import Notifier
from .pipeline import Radar, scope_listings, target_terms
from .rules import passes_global_filters, score_listing

UI_FILE = Path(__file__).resolve().parent / "ui" / "index.html"
DEFAULT_PORT = 8765
MAX_PAGE_SIZE = 200


def _plain(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: _plain(getattr(obj, f.name)) for f in fields(obj)}
    if isinstance(obj, (list, tuple, set)):
        return [_plain(item) for item in obj]
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


@dataclass
class FetchState:
    running: bool = False
    phase: str = "idle"
    log: list[str] = field(default_factory=list)
    fetched: int = 0
    new_items: int = 0
    notified: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str = ""

    def reset(self) -> None:
        self.phase = "running"
        self.log = []
        self.fetched = 0
        self.new_items = 0
        self.notified = 0
        self.error = ""
        self.started_at = time.time()
        self.finished_at = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "running": self.running,
            "phase": self.phase,
            "log": self.log[-60:],
            "fetched": self.fetched,
            "new_items": self.new_items,
            "notified": self.notified,
            "elapsed": round(time.time() - (self.started_at or time.time()), 1),
            "error": self.error,
        }


class AppState:
    def __init__(self, config_path: str | None) -> None:
        self.config_path = config_path
        self.config = load_config(config_path)
        self.fetch_state = FetchState()
        self._lock = threading.Lock()
        self.db = Database(self.config.db_path)
        self._catalog: Any = None

    def reload(self) -> None:
        self.config = load_config(self.config_path)

    @property
    def catalog(self) -> Any:
        if self._catalog is None:
            cat = catalog_mod.load_catalog(self.config.catalog_path)
            if not len(cat) and self.config.filters_path.is_file():
                cat = catalog_mod.load_from_filters_file(
                    str(self.config.filters_path), self.config.search.category_id
                )
                if len(cat):
                    cat.save(self.config.catalog_path)
            self._catalog = cat
        return self._catalog

    def refresh_catalog(self) -> int:
        cat = catalog_mod.load_from_filters_file(
            str(self.config.filters_path), self.config.search.category_id
        )
        if len(cat):
            cat.save(self.config.catalog_path)
            self._catalog = cat
        return len(self._catalog)

    def snapshot(self) -> Any:
        return Radar(self.config, Notifier(console=False, desktop=False)).snapshot(self.db)

    def market_summary(self) -> dict[str, Any]:
        snapshot = self.snapshot()
        catalog = self.catalog
        return {
            "sample": snapshot.sample_size,
            "overall": asdict(snapshot.overall),
            "vehicles": {
                path: {"stats": asdict(stats), "label": _label_for(catalog, path)}
                for path, stats in sorted(
                    snapshot.by_vehicle.items(), key=lambda kv: kv[1].count, reverse=True
                )
            },
            "brands": {
                path: {"stats": asdict(stats), "label": _label_for(catalog, path)}
                for path, stats in sorted(
                    snapshot.by_brand.items(), key=lambda kv: kv[1].count, reverse=True
                )
            },
            "by_year": {str(k): asdict(v) for k, v in sorted(snapshot.by_year.items())},
        }

    def db_stats(self) -> dict[str, int]:
        items = self.db.query(limit=100000)
        return {
            "total": len(items),
            "priced": sum(1 for i in items if i.price.toman),
            "with_model": sum(1 for i in items if i.vehicle_path),
            "with_year": sum(1 for i in items if i.year),
            "with_mileage": sum(1 for i in items if i.mileage_km),
            "with_specs": sum(1 for i in items if i.specs_fetched),
            "bookmarked": self.db.count_bookmarked(),
            "business": sum(1 for i in items if i.business),
        }

    def close(self) -> None:
        self.db.close()


def _label_for(catalog: Any, path: str) -> str:
    if catalog is not None and len(catalog):
        model = catalog.resolve_path(path)
        if model is not None:
            return model.fa or model.name
    return path


def _int(raw: str | None, default: int | None = None) -> int | None:
    if raw in (None, "", "null", "undefined", "all"):
        return default
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        return default


def _float(raw: str | None, default: float | None = None) -> float | None:
    if raw in (None, "", "null", "undefined", "all"):
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _first(params: dict[str, list[str]], key: str, default: str = "") -> str:
    return params.get(key, [default])[0]


def listing_payload(listing: Listing, snapshot: Any, flip: Any = None) -> dict[str, Any]:
    from .market import profit_margin

    verdict = compare_to_market(
        listing.price.toman, snapshot, listing.vehicle_path, listing.year
    )
    offer = suggest_offer(verdict)
    if flip is None:
        _, margin = profit_margin(
            listing.price.toman, snapshot, listing.vehicle_path, listing.year
        )
        flip_total = None
        flags: tuple[str, ...] = ()
    else:
        margin = flip.margin_toman
        flip_total = flip.total
        flags = flip.flags
    payload = {
        "id": listing.listing_id,
        "title": listing.title,
        "path": listing.vehicle_path,
        "name": listing.vehicle_name,
        "label": listing.label,
        "brand": listing.brand_fa,
        "year": listing.year,
        "year_g": listing.gregorian_year,
        "mileage": listing.mileage_km,
        "price": listing.price.toman,
        "price_text": text_utils.format_toman_full(listing.price.toman),
        "negotiable": listing.price.negotiable,
        "city": listing.city,
        "district": listing.district,
        "business": listing.business,
        "url": listing.url,
        "score": listing.score,
        "first_seen": listing.first_seen_at.isoformat(),
        "created_at": listing.created_at.isoformat() if listing.created_at else None,
        "published_at": listing.published_iso,
        "published_text": listing.published_text,
        "paint": listing.paint,
        "chassis": listing.chassis,
        "body_condition": listing.body_condition,
        "engine": listing.engine,
        "gearbox": listing.gearbox,
        "fuel": listing.fuel,
        "insurance": listing.insurance,
        "trim": listing.trim,
        "body_type": listing.body_type,
        "specs_fetched": listing.specs_fetched,
        "bookmarked": listing.bookmarked,
        "dismissed": listing.dismissed,
        "noted_at": listing.noted_at.isoformat() if listing.noted_at else None,
        "baseline": verdict.baseline_median,
        "delta_ratio": verdict.delta_ratio,
        "label_market": verdict.label,
        "offer": offer,
        "margin": margin,
        "margin_ratio": flip.margin_ratio if flip else None,
        "flip_score": flip_total,
        "flags": list(flags),
    }
    return payload


def deal_payload(result: Any) -> dict[str, Any]:
    """Flatten a DealResult into the shape the interface renders."""
    deal = result.deal
    return {
        "verdict": deal.verdict,
        "profit": deal.profit_toman,
        "roi": deal.roi,
        "resale": deal.resale_toman,
        "factor": deal.discount_factor,
        "age_hours": deal.age_hours,
        "reasons": list(deal.reasons),
        "blockers": list(deal.blockers),
        "good": list(result.good),
        "bad": list(result.bad),
        "detail": result.detail,
    }


def query_listings(app: AppState, params: dict[str, list[str]]) -> dict[str, Any]:
    from .market import flip_score

    paths = [p for p in _first(params, "paths").split(",") if p]
    brands = [p for p in _first(params, "brands").split(",") if p]
    min_year = _int(_first(params, "min_year"))
    max_year = _int(_first(params, "max_year"))
    max_km = _int(_first(params, "max_mileage"))
    min_km = _int(_first(params, "min_mileage"))
    min_price = _int(_first(params, "min_price"))
    max_price = _int(_first(params, "max_price"))
    max_discount = _float(_first(params, "max_discount"))
    min_margin = _int(_first(params, "min_margin"))
    max_age = _int(_first(params, "max_publish_age"))
    min_flip = _float(_first(params, "min_flip"))
    chassis = _first(params, "chassis") or None
    body_cond = _first(params, "body") or None
    min_score = _float(_first(params, "min_score"), 0.0) or 0.0
    exclude_business = _first(params, "exclude_business", "1") in ("1", "true", "True")
    exclude_suspicious = _first(params, "exclude_suspicious", "1") in ("1", "true", "True")
    only_new = _first(params, "only_new", "0") in ("1", "true", "True")
    only_bookmarked = _first(params, "bookmarked", "0") in ("1", "true", "True")
    hide_dismissed = _first(params, "hide_dismissed", "1") in ("1", "true", "True")
    search = _first(params, "q").strip()
    sort = _first(params, "sort", "newest")
    limit = min(_int(_first(params, "limit"), 200) or 200, MAX_PAGE_SIZE)

    snapshot = app.snapshot()
    weights = app.config.scoring.weights
    pool = app.db.query(
        limit=20000,
        vehicle_paths=paths or None,
        brand_paths=brands or None,
        bookmarked=True if only_bookmarked else None,
        dismissed=False if hide_dismissed else None,
        chassis=chassis,
        body_condition=body_cond,
    )

    rows: list[dict[str, Any]] = []
    for listing in pool:
        if exclude_business and listing.business:
            continue
        if min_year and (listing.year is None or listing.year < min_year):
            continue
        if max_year and (listing.year is None or listing.year > max_year):
            continue
        if max_km and (listing.mileage_km is None or listing.mileage_km > max_km):
            continue
        if min_km and (listing.mileage_km is not None and listing.mileage_km < min_km):
            continue
        if min_price and (listing.price.toman is None or listing.price.toman < min_price):
            continue
        if max_price and (listing.price.toman is None or listing.price.toman > max_price):
            continue
        if only_new and listing.dismissed:
            continue
        if search:
            haystack = f"{listing.title} {listing.district or ''} {listing.label}".lower()
            if search.lower() not in haystack:
                continue

        score = score_listing(listing, snapshot, weights)
        if score < min_score:
            continue

        flip = flip_score(
            listing,
            snapshot,
            reference_year=app.config.scoring.reference_year,
            reference_mileage=app.config.scoring.reference_mileage_km,
        )
        payload = listing_payload(listing, snapshot, flip)

        if exclude_suspicious and payload["label_market"] == SUSPICIOUS_LABEL:
            continue
        if max_discount is not None:
            ratio = payload["delta_ratio"]
            if ratio is None or ratio > -max_discount:
                continue
        if min_margin and (payload["margin"] or 0) < min_margin:
            continue
        if min_flip and (payload["flip_score"] or 0) < min_flip:
            continue
        if max_age:
            published = listing.published_at
            if published is None:
                continue
            if (utcnow() - published).total_seconds() / 3600 > max_age:
                continue

        rows.append(payload)

    def key_for(name: str):
        if name == "price_asc":
            return lambda r: (r["price"] is None, r["price"] or 0)
        if name == "price_desc":
            return lambda r: (r["price"] is None, -(r["price"] or 0))
        if name == "score":
            return lambda r: -r["score"]
        if name == "flip":
            return lambda r: -r["flip_score"]
        if name == "margin":
            return lambda r: -(r["margin"] or 0)
        if name == "published":
            return lambda r: r["published_at"] or ""
        if name == "year_desc":
            return lambda r: -(r["year"] or 0)
        if name == "discount":
            return lambda r: (
                r["delta_ratio"] is None,
                r["delta_ratio"] if r["delta_ratio"] is not None else 1,
            )
        return lambda r: r["first_seen"]

    rows.sort(key=key_for(sort), reverse=sort in ("newest", "published"))
    return {
        "items": rows[:limit],
        "total": len(rows),
        "market_sample": snapshot.sample_size,
    }


def _deal_settings(app: AppState, params: dict[str, list[str]]) -> Any:
    """Config defaults, overridable per-request so the UI can explore freely."""
    from .deals import DealSettings

    base = app.config.deals
    settings = DealSettings(
        min_profit_toman=_int(_first(params, "min_profit"), base.min_profit_toman)
        or 0,
        min_roi=_float(_first(params, "min_roi"), base.min_roi),
        max_age_hours=_int(_first(params, "max_age"), base.max_age_hours),
        require_clean_body=_flag(params, "clean_body", base.require_clean_body),
        require_sound_chassis=_flag(params, "sound_chassis", base.require_sound_chassis),
        require_known_condition=_flag(
            params, "known_condition", base.require_known_condition
        ),
        exclude_business=_flag(params, "exclude_business", base.exclude_business),
        max_mileage_km=_int(_first(params, "max_mileage"), base.max_mileage_km),
        min_year=_int(_first(params, "min_year"), base.min_year),
        max_year=_int(_first(params, "max_year"), base.max_year),
        max_price_toman=_int(_first(params, "max_price"), base.max_price_toman),
        exclude_salvage=_flag(params, "no_salvage", base.exclude_salvage),
    )
    return settings


def _flag(params: dict[str, list[str]], key: str, default: bool) -> bool:
    raw = params.get(key, [None])[0]
    if raw is None or raw == "":
        return default
    return raw in ("1", "true", "True", "yes", "on")


def deal_settings_payload(app: AppState) -> dict[str, Any]:
    """Expose the criteria so the UI can show and change them."""
    from . import deals as deals_mod

    base = app.config.deals
    snapshot = app.snapshot()
    pending = len(app.db.needs_specs(limit=500))
    return {
        "min_profit_toman": base.min_profit_toman,
        "min_roi": base.min_roi,
        "max_age_hours": base.max_age_hours,
        "require_clean_body": base.require_clean_body,
        "require_sound_chassis": base.require_sound_chassis,
        "require_known_condition": base.require_known_condition,
        "exclude_business": base.exclude_business,
        "max_mileage_km": base.max_mileage_km,
        "min_year": base.min_year,
        "max_year": base.max_year,
        "max_price_toman": base.max_price_toman,
        "exclude_salvage": base.exclude_salvage,
        "market_sample": snapshot.sample_size,
        "pending_specs": pending,
        "reference_free_km": deals_mod.MILEAGE_FREE_KM,
    }


def query_deals(app: AppState, params: dict[str, list[str]]) -> dict[str, Any]:
    """The primary screen: only cars worth acting on, best profit first."""
    from .deals import evaluate_deal, rank_results, summarize
    from .market import flip_score

    settings = _deal_settings(app, params)
    snapshot = app.snapshot()
    include_pass = _flag(params, "include_pass", False)
    only_bookmarked = _flag(params, "bookmarked", False)
    hide_dismissed = _flag(params, "hide_dismissed", True)
    limit = min(_int(_first(params, "limit"), 100) or 100, MAX_PAGE_SIZE)

    pool = app.db.query(
        limit=20000,
        bookmarked=True if only_bookmarked else None,
        dismissed=False if hide_dismissed else None,
    )

    # evaluate once, then reuse for both totals and the visible rows
    results = [evaluate_deal(item, snapshot, settings) for item in pool]
    totals = summarize(results)
    ranked = rank_results(results, include_pass=include_pass)

    rows: list[dict[str, Any]] = []
    for result in ranked[:limit]:
        listing = result.listing
        flip = flip_score(
            listing,
            snapshot,
            reference_year=app.config.scoring.reference_year,
            reference_mileage=app.config.scoring.reference_mileage_km,
        )
        payload = listing_payload(listing, snapshot, flip)
        payload["deal"] = deal_payload(result)
        rows.append(payload)

    return {
        "items": rows,
        "total": len(ranked),
        "summary": totals,
        "market_sample": snapshot.sample_size,
        "criteria": deal_settings_payload(app),
        "hint": _deal_hint(totals, ranked, app),
    }


def _deal_hint(totals: dict[str, int], ranked: list[Any], app: AppState) -> str | None:
    """When the screen is empty, say exactly what the user should do next."""
    if totals.get("buy") or totals.get("watch"):
        if len(ranked) < 25 and app.config.search.pages < 6:
            return "برای دیدن آگهی‌های بیشتر، تعداد صفحه‌ها را در config.json زیاد کنید."
        return None
    pending = len(app.db.needs_specs(limit=500))
    if pending:
        return (
            f"{pending} آگهی هنوز مشخصاتش خوانده نشده. با فرمان specs شرایطشان را بخوان: "
            "run.cmd specs --limit 50"
        )
    if totals.get("considered", 0) < 30:
        return "هنوز آگهی کافی برای محاسبهٔ میانهٔ بازار جمع نشده. چند بار «دریافت» بزنید."
    return "با معیارهای فعلی سودداری پیدا نشد. حداقل سود یا تازگی را در فیلترها باز کنید."


class _WebNotifier(Notifier):
    def __init__(self, state: FetchState) -> None:
        super().__init__(console=False, desktop=False, color=False)
        self.state = state

    def info(self, message: str) -> None:
        self.state.log.append(message)

    def ok(self, message: str) -> None:
        self.state.log.append(f"✓ {message}")

    def warn(self, message: str) -> None:
        self.state.log.append(f"! {message}")

    def error(self, message: str) -> None:
        self.state.log.append(f"× {message}")

    def hit(self, result: Any) -> None:
        listing = result.listing
        self.state.log.append(
            f"★ {listing.label} {listing.year or ''} | "
            f"{text_utils.format_toman(listing.price.toman)} | {result.rule.name} | "
            f"{result.verdict.label} | {listing.url or ''}"
        )


def _enrich_new(
    client: DivarClient,
    db: Database,
    listings: list[Listing],
    state: FetchState,
    budget: int,
) -> int:
    """Open only the post pages we have not read specs for yet."""
    from .parse import apply_specs

    fresh = []
    for listing in listings:
        existing = db.get(listing.listing_id)
        if existing is None or not existing.specs_fetched:
            fresh.append(listing)
    fresh = fresh[:budget]
    if not fresh:
        return budget
    state.log.append(f"  باز کردن {len(fresh)} صفحهٔ جزئیات برای مشخصات…")
    for listing in fresh:
        try:
            apply_specs(listing, client.fetch_specs(listing.listing_id))
        except ClientError as exc:
            listing.specs_fetched = True
            state.log.append(f"  ! {listing.listing_id}: {exc}")
        except Exception as exc:  # noqa: BLE001
            # never swallow silently: a crash here looks like "no specs" later
            state.log.append(
                f"  × خطای مشخصات {listing.listing_id}: {type(exc).__name__}: {exc}"
            )
    return budget - len(fresh)


def start_fetch(app: AppState, pages: int) -> dict[str, Any]:
    with app._lock:
        if app.fetch_state.running:
            return {"started": False, "reason": "در حال اجرا"}
        app.fetch_state.reset()
        app.fetch_state.running = True

    targets = app.config.fetch_targets()

    def worker() -> None:
        state = app.fetch_state
        notifier = _WebNotifier(state)
        try:
            targets = app.config.fetch_targets()
            if not targets:
                raise ClientError(
                    "هیچ هدف فعالی تعریف نشده است. در config.json بخش targets را "
                    'تنظیم کنید یا "search": {"fetch_all_cars": true} را بگذارید.'
                )

            def fetcher():
                with DivarClient(
                    headless=True, category=app.config.search.category_id
                ) as client:
                    budget = app.config.watch.max_specs_per_run
                    with Database(app.config.db_path) as db:
                        for target in targets:
                            terms = target_terms(app.catalog, target)
                            state.phase = f"fetching {target.label}"
                            state.log.append(
                                f"هدف: {target.label}"
                                + (f" | فیلتر: {', '.join(terms)}" if terms else "")
                            )
                            pagination = None
                            total = min(
                                pages or app.config.watch.max_pages_per_run,
                                app.config.search.pages,
                            )
                            for number in range(1, total + 1):
                                result = client.search(
                                    query=target.query,
                                    category=app.config.search.category_id,
                                    city_id=app.config.search.city_id,
                                    page=number,
                                    sort=app.config.search.sort,
                                    pagination=pagination,
                                    brand_model=terms or None,
                                )
                                scoped, dropped = scope_listings(
                                    result.listings, target
                                )
                                note = (
                                    f" ({dropped} خارج از هدف حذف شد)"
                                    if dropped
                                    else ""
                                )
                                state.log.append(
                                    f"  صفحه {number}: {len(scoped)} از "
                                    f"{result.total_rows} ردیف{note}"
                                )
                                pagination = result.pagination
                                if budget > 0 and scoped:
                                    budget = _enrich_new(
                                        client, db, scoped, state, budget
                                    )
                                yield scoped
                                if not pagination:
                                    break
                                time.sleep(
                                    app.config.watch.delay_min_seconds
                                    + (
                                        app.config.watch.delay_max_seconds
                                        - app.config.watch.delay_min_seconds
                                    )
                                    * 0.5
                                )

            state.phase = "analyzing"
            radar = Radar(app.config, notifier)
            report = radar.run_once(app.db, fetcher)
            state.fetched = report.fetched
            state.new_items = report.new_items
            state.notified = report.notified
            state.log.append(
                f"پایان: {report.fetched} دریافت | {report.new_items} جدید | "
                f"{report.notified} اعلان"
            )
            state.phase = "done"
        except ClientError as exc:
            state.error = str(exc)
            state.log.append(f"خطا: {exc}")
            state.phase = "error"
        except Exception as exc:  # noqa: BLE001
            state.error = f"{type(exc).__name__}: {exc}"
            state.log.append(f"خطای غیرمنتظره: {exc}")
            state.log.append(traceback.format_exc()[-900:])
            state.phase = "error"
        finally:
            state.running = False
            state.finished_at = time.time()

    threading.Thread(target=worker, daemon=True, name="pride-fetch").start()
    return {"started": True}


def catalog_payload(app: AppState, category: str) -> dict[str, Any]:
    catalog = app.catalog
    if not len(catalog):
        return {"brands": [], "models": []}
    brands = [
        {"path": b.path, "label": b.fa or b.name, "name": b.name}
        for b in catalog.brands
    ]
    models = [
        {"path": m.path, "label": m.fa or m.name, "brand": m.brand_path}
        for m in catalog.models
        if m.depth <= 2
    ]
    return {
        "brands": sorted(brands, key=lambda b: b["label"]),
        "models": sorted(models, key=lambda m: m["label"]),
        "count": len(catalog),
    }


def build_handler(app: AppState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = f"PrideRadar/{__version__}"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            return

        def _send(self, status: int, body: bytes, content_type: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, data: Any, status: int = 200) -> None:
            self._send(
                status, json.dumps(data, ensure_ascii=False).encode("utf-8"),
                "application/json; charset=utf-8",
            )

        def _html(self, text: str) -> None:
            self._send(200, text.encode("utf-8"), "text/html; charset=utf-8")

        def _body(self) -> dict[str, Any]:
            length = _int(self.headers.get("Content-Length", "0"), 0) or 0
            if not length:
                return {}
            try:
                return json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                return {}

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            route = parsed.path
            params = parse_qs(parsed.query)

            if route in ("/", "/index.html"):
                if not UI_FILE.is_file():
                    self._json({"error": f"UI file missing: {UI_FILE}"}, 500)
                    return
                self._html(UI_FILE.read_text(encoding="utf-8"))
                return

            if route == "/api/bootstrap":
                targets = app.config.active_targets
                primary = targets[0] if targets else None
                self._json(
                    {
                        "version": __version__,
                        "config": _plain(app.config),
                        "market": app.market_summary(),
                        "stats": app.db_stats(),
                        "fetch": app.fetch_state.to_dict(),
                        "catalog": catalog_payload(app, app.config.search.category_id),
                        "web_url": page_url(
                            app.config.search.city,
                            app.config.search.category,
                            "",
                            target_terms(app.catalog, primary) if primary else None,
                        ),
                    }
                )
                return

            if route == "/api/listings":
                self._json(query_listings(app, params))
                return

            if route == "/api/deals":
                self._json(query_deals(app, params))
                return

            if route == "/api/settings":
                self._json(deal_settings_payload(app))
                return

            if route == "/api/market":
                self._json(app.market_summary())
                return

            if route == "/api/stats":
                self._json(app.db_stats())
                return

            if route == "/api/catalog":
                self._json(catalog_payload(app, _first(params, "category", "light")))
                return

            if route == "/api/bookmarks":
                from .market import flip_score

                snapshot = app.snapshot()
                items = app.db.bookmarked(limit=500)
                rows = []
                for listing in items:
                    flip = flip_score(
                        listing,
                        snapshot,
                        reference_year=app.config.scoring.reference_year,
                        reference_mileage=app.config.scoring.reference_mileage_km,
                    )
                    rows.append(listing_payload(listing, snapshot, flip))
                rows.sort(key=lambda r: r["noted_at"] or "", reverse=True)
                self._json({"items": rows, "total": len(rows)})
                return

            if route == "/api/fetch/status":
                self._json(app.fetch_state.to_dict())
                return

            if route.startswith("/api/listing/"):
                listing_id = unquote(route.rsplit("/", 1)[-1])
                listing = app.db.get(listing_id)
                if not listing:
                    self._json({"error": "not found"}, 404)
                    return
                from .deals import evaluate_deal
                from .market import flip_score

                snapshot = app.snapshot()
                flip = flip_score(
                    listing,
                    snapshot,
                    reference_year=app.config.scoring.reference_year,
                    reference_mileage=app.config.scoring.reference_mileage_km,
                )
                payload = listing_payload(listing, snapshot, flip)
                # the sheet shows resale value and profit, so it needs the deal too
                payload["deal"] = deal_payload(
                    evaluate_deal(listing, snapshot, _deal_settings(app, params))
                )
                self._json(payload)
                return

            if route == "/api/rules":
                self._json({"rules": [_plain(rule) for rule in app.config.rules]})
                return

            self._json({"error": "not found"}, 404)

        def do_POST(self) -> None:  # noqa: N802
            route = urlparse(self.path).path
            body = self._body()

            if route == "/api/fetch":
                pages = _int(str(body.get("pages", "")), 0) or 0
                self._json(start_fetch(app, pages))
                return

            if route == "/api/catalog/refresh":
                self._json({"count": app.refresh_catalog()})
                return

            if route == "/api/bookmark":
                listing_id = str(body.get("id") or "")
                value = bool(body.get("value", True))
                if not listing_id or not app.db.get(listing_id):
                    self._json({"error": "not found"}, 404)
                    return
                app.db.set_bookmark(listing_id, value)
                self._json({"ok": True, "id": listing_id, "bookmarked": value,
                            "count": app.db.count_bookmarked()})
                return

            if route == "/api/dismiss":
                listing_id = str(body.get("id") or "")
                value = bool(body.get("value", True))
                if not listing_id or not app.db.get(listing_id):
                    self._json({"error": "not found"}, 404)
                    return
                app.db.set_dismissed(listing_id, value)
                self._json({"ok": True, "id": listing_id, "dismissed": value})
                return

            if route == "/api/config":
                self._save_config(body)
                return

            self._json({"error": "not found"}, 404)

        def _save_config(self, body: dict[str, Any]) -> None:
            path = Path(app.config_path or (Path.cwd() / "config.json"))
            current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
            for section in ("search", "watch", "filters", "notify", "storage", "scoring"):
                if isinstance(body.get(section), dict):
                    current.setdefault(section, {}).update(body[section])
            for key in ("targets", "rules"):
                if isinstance(body.get(key), list):
                    current[key] = body[key]
            try:
                path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as exc:
                self._json({"error": f"ذخیره نشد: {exc}"}, 500)
                return
            try:
                app.reload()
            except (ValueError, FileNotFoundError) as exc:
                self._json({"error": f"تنظیمات نامعتبر: {exc}"}, 400)
                return
            self._json({"saved": True, "config": _plain(app.config)})

    return Handler


def serve(
    config_path: str | None = None, port: int = DEFAULT_PORT, open_browser: bool = True
) -> int:
    app = AppState(config_path)
    handler = build_handler(app)
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    server.daemon_threads = True

    url = f"http://127.0.0.1:{port}/"
    targets = "، ".join(t.label for t in app.config.active_targets) or "همهٔ خودروها"
    print(f"رابط کاربری روی {url} بالا آمد")
    print(f"شهر: {app.config.search.city} | اهداف: {targets}")
    print(f"کاتالوگ: {len(app.catalog)} خودرو | دیتابیس: {app.config.db_path}")
    print("برای خروج Ctrl+C بزن")
    print("برای موبایل، آدرس شبکه محلی همین پورت را باز کنید (مثلاً http://<IP>:{port})")

    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nدر حال خروج ...")
    finally:
        server.shutdown()
        server.server_close()
        app.close()
    return 0
