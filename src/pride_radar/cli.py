"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import fields
from datetime import timedelta
from pathlib import Path

from . import __version__
from . import catalog as catalog_mod
from . import text as text_utils
from .client import ClientError, DivarClient, page_url
from .config import Config, VehicleTarget, load_config
from .db import Database
from .market import compare_to_market, suggest_offer
from .models import utcnow
from .notify import Notifier
from .parse import apply_specs, parse_search_payload
from .pipeline import Radar, scope_listings, target_terms


def _notifier(config: Config) -> Notifier:
    return Notifier(
        console=config.notify.console,
        desktop=config.notify.desktop,
        color=config.notify.color,
    )


def _load(config_path: str | None) -> Config:
    try:
        return load_config(config_path)
    except (FileNotFoundError, ValueError) as exc:
        print(f"خطا در تنظیمات: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


def _catalog(config: Config) -> catalog_mod.Catalog:
    cat = catalog_mod.load_catalog(config.catalog_path)
    if not len(cat) and config.filters_path.is_file():
        cat = catalog_mod.load_from_filters_file(
            str(config.filters_path), config.search.category_id
        )
        if len(cat):
            cat.save(config.catalog_path)
    return cat


def _targets(config: Config) -> list[VehicleTarget]:
    targets = config.fetch_targets()
    if not targets:
        raise SystemExit(
            "هیچ هدف فعالی تعریف نشده است.\n"
            "در config.json بخش targets را تنظیم کنید، یا برای اسکن کل شهر:\n"
            '  "search": { "fetch_all_cars": true }'
        )
    return targets


def _fetcher(config: Config, notifier: Notifier, pages: int | None, with_specs: bool = True):
    max_specs = config.watch.max_specs_per_run

    def factory():
        targets = _targets(config)
        cat = _catalog(config)
        spec_budget = max_specs
        with Database(config.db_path) as db:
            with DivarClient(headless=True, category=config.search.category_id) as client:
                for target in targets:
                    terms = target_terms(cat, target)
                    pagination = None
                    total = min(pages or config.watch.max_pages_per_run, config.search.pages)
                    notifier.info(
                        f"هدف: {target.label}"
                        + (f" | فیلتر: {', '.join(terms)}" if terms else " (همهٔ خودروها)")
                    )
                    for number in range(1, total + 1):
                        result = client.search(
                            query=target.query,
                            category=config.search.category_id,
                            city_id=config.search.city_id,
                            page=number,
                            sort=config.search.sort,
                            pagination=pagination,
                            brand_model=terms or None,
                        )
                        scoped, dropped = scope_listings(result.listings, target)
                        note = f" ({dropped} خارج از هدف حذف شد)" if dropped else ""
                        notifier.info(
                            f"  صفحه {number}: {len(scoped)} از {result.total_rows} ردیف{note}"
                        )
                        pagination = result.pagination
                        if with_specs and spec_budget > 0 and scoped:
                            spec_budget = _enrich_new(
                                client, db, scoped, notifier, spec_budget
                            )
                        yield scoped
                        if not pagination:
                            break

    return factory


def _enrich_new(
    client: DivarClient,
    db: Database,
    listings: list[Any],
    notifier: Notifier,
    budget: int,
) -> int:
    """Open the post page only for listings we have not seen specs for yet."""
    fresh = []
    for listing in listings:
        existing = db.get(listing.listing_id)
        if existing is None or not existing.specs_fetched:
            fresh.append(listing)
    fresh = fresh[:budget]
    if not fresh:
        return budget
    notifier.info(f"  باز کردن {len(fresh)} صفحهٔ جزئیات برای مشخصات…")
    for listing in fresh:
        try:
            apply_specs(listing, client.fetch_specs(listing.listing_id))
        except ClientError:
            listing.specs_fetched = True
        except Exception as exc:  # noqa: BLE001
            notifier.error(
                f"خطای مشخصات {listing.listing_id}: {type(exc).__name__}: {exc}"
            )
    return budget - len(fresh)


def cmd_web(args: argparse.Namespace) -> int:
    from .web import serve

    return serve(args.config, port=args.port, open_browser=not args.no_browser)


def cmd_check(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    notifier.desktop = False
    cat = _catalog(config)
    notifier.banner("بررسی اتصال به دیوار")
    notifier.info(f"شهر: {config.search.city} (id={config.search.city_id})")
    notifier.info(f"دسته: {config.search.category_id} | کاتالوگ: {len(cat)} خودرو")

    targets = _targets(config)
    for target in targets:
        terms = target_terms(cat, target)
        notifier.info(f"هدف: {target.label} → {', '.join(terms) if terms else 'بدون فیلتر برند'}")
        try:
            with DivarClient(headless=True, category=config.search.category_id) as client:
                result = client.search(
                    query=target.query,
                    category=config.search.category_id,
                    city_id=config.search.city_id,
                    sort=config.search.sort,
                    brand_model=terms or None,
                )
        except ClientError as exc:
            notifier.error(str(exc))
            continue
        notifier.ok(f"{len(result.listings)} خودرو از {result.total_rows} ردیف")
        if result.breadcrumb:
            notifier.info("  مسیر: " + " > ".join(result.breadcrumb[:5]))
        for listing in result.listings[:5]:
            notifier.info(
                f"    {listing.label:18} {listing.year or '؟'} | "
                f"{text_utils.format_toman_full(listing.price.toman):20} | "
                f"{listing.mileage_km or 0:>7,} کیلومتر | {listing.district or '؟'}"
            )
    return 0


def cmd_fetch(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    notifier.desktop = False
    with Database(config.db_path) as db:
        radar = Radar(config, notifier)
        try:
            report = radar.run_once(
                db, _fetcher(config, notifier, args.pages, not args.no_specs)
            )
        except ClientError as exc:
            notifier.error(str(exc))
            return 1
        notifier.summary(report.fetched, report.new_items, report.notified)
    return 0


def cmd_specs(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    notifier.desktop = False
    with Database(config.db_path) as db:
        pending = db.needs_specs(limit=args.limit)
        if not pending:
            notifier.info("آگهی بدون مشخصات باقی نمانده است.")
            return 0
        notifier.banner(f"دریافت مشخصات {len(pending)} آگهی")
        with DivarClient(headless=True, category=config.search.category_id) as client:
            for i, listing_id in enumerate(pending, 1):
                listing = db.get(listing_id)
                if not listing:
                    continue
                try:
                    apply_specs(listing, client.fetch_specs(listing_id))
                except ClientError as exc:
                    db.mark_specs_fetched(listing_id)
                    notifier.warn(f"{listing_id}: {exc}")
                    continue
                db.upsert(listing)
                notifier.info(
                    f"  {i}/{len(pending)} {listing.label} | "
                    f"رنگ {listing.paint or '؟'} | شاسی {listing.chassis or '؟'} | "
                    f"بدنه {listing.body_condition or '؟'} | "
                    f"گیربکس {listing.gearbox_state or '؟'} | "
                    f"انتشار {listing.published_text or '؟'}"
                )
    return 0


def cmd_bookmarks(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    with Database(config.db_path) as db:
        if args.remove:
            db.set_bookmark(args.remove, False)
            notifier.ok(f"بوکمارک {args.remove} حذف شد")
            return 0
        items = db.bookmarked(limit=500)
        notifier.banner(f"لیست بوکمارک - {len(items)} مورد")
        if not items:
            notifier.info("هنوز چیزی بوکمارک نکرده‌ای.")
            return 0
        for item in items:
            notifier.info(
                f"  [{item.score or 0:>5.1f}] {item.label} {item.year or '؟'} | "
                f"{text_utils.format_toman(item.price.toman)} | "
                f"{item.published_text or '؟'} | {item.district or '؟'}"
            )
            notifier.info(f"      {item.title[:70]}")
            notifier.info(f"      {item.url}")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    if args.pages:
        config.watch.max_pages_per_run = args.pages
    if args.runs is not None:
        config.watch.max_runs = args.runs
    with Database(config.db_path) as db:
        Radar(config, notifier).watch(db, _fetcher(config, notifier, args.pages))
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    with Database(config.db_path) as db:
        since = utcnow() - timedelta(days=args.days)
        listings = db.query(limit=5000, since=since)
        priced = [i for i in listings if i.price.toman]
        snapshot = Radar(config, notifier).snapshot(db)
        cat = _catalog(config)

        notifier.banner(f"گزارش بازار {config.search.city} - {args.days} روز اخیر")
        notifier.info(f"کل آگهی‌ها: {len(listings)} | قیمت‌دار: {len(priced)}")
        if not priced:
            notifier.warn("داده‌ای برای تحلیل نیست. اول fetch یا watch را اجرا کنید.")
            return 1

        notifier.info(f"میانهٔ کل بازار: {text_utils.format_toman(snapshot.overall.median)}")

        top = sorted(snapshot.by_vehicle.items(), key=lambda kv: kv[1].count, reverse=True)[:12]
        if top:
            notifier.info("")
            notifier.info("میانه به تفکیک مدل:")
            for path, st in top:
                model = cat.resolve_path(path)
                label = (model.fa or model.name) if model else path
                notifier.info(
                    f"    {label:22} نمونه {st.count:3} | "
                    f"کف {text_utils.format_toman(st.minimum):>11} | "
                    f"میانه {text_utils.format_toman(st.median):>11} | "
                    f"سقف {text_utils.format_toman(st.maximum):>11}"
                )

        if snapshot.by_year:
            notifier.info("")
            notifier.info("میانه به تفکیک سال:")
            for year in sorted(snapshot.by_year, reverse=True)[:12]:
                st = snapshot.by_year[year]
                notifier.info(
                    f"    {year} ({year + 621})  نمونه {st.count:3} | "
                    f"{text_utils.format_toman(st.median):>12}"
                )

        drops = db.recent_price_drops(limit=8)
        if drops:
            notifier.info("")
            notifier.info("بیشترین کاهش قیمت ثبت‌شده:")
            for item in drops:
                history = db.price_history(item.listing_id)
                if len(history) >= 2 and history[-2][1] and item.price.toman:
                    delta = history[-2][1] - item.price.toman
                    pct = delta / history[-2][1] * 100
                    notifier.info(
                        f"    {item.label} {item.year or '؟'} | "
                        f"{text_utils.format_toman_full(history[-2][1])} ← "
                        f"{text_utils.format_toman_full(item.price.toman)} ({pct:.1f}٪ کاهش)"
                    )

        if args.suggest:
            notifier.info("")
            notifier.info("پیشنهاد قیمت برای آگهی‌های جدید:")
            fresh = [i for i in listings if i.price.toman and i.first_seen_at >= since]
            for item in fresh[: args.suggest]:
                verdict = compare_to_market(
                    item.price.toman, snapshot, item.vehicle_path, item.year
                )
                offer = suggest_offer(verdict)
                offer_text = text_utils.format_toman(offer) if offer else "پیشنهاد نمی‌شود"
                notifier.info(
                    f"    {item.label} {item.year or '؟'} | "
                    f"آگهی {text_utils.format_toman(item.price.toman)} | "
                    f"میانه {text_utils.format_toman(verdict.baseline_median)} | "
                    f"پیشنهاد شما: {offer_text} | {verdict.label}"
                )
    return 0


def cmd_price(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    with Database(config.db_path) as db:
        snapshot = Radar(config, notifier).snapshot(db)
        notifier.banner("برآورد قیمت")
        notifier.info(
            f"مبنای بازار: {snapshot.sample_size} آگهی، "
            f"میانه {text_utils.format_toman(snapshot.overall.median)}"
        )
        for target in args.listing:
            listing = db.get(target)
            if not listing:
                notifier.warn(f"آگهی {target} پیدا نشد")
                continue
            verdict = compare_to_market(
                listing.price.toman, snapshot, listing.vehicle_path, listing.year
            )
            offer = suggest_offer(verdict)
            notifier.info(f"  {listing.title[:50]}")
            notifier.info(
                f"    قیمت آگهی {text_utils.format_toman_full(listing.price.toman)} | "
                f"میانه {text_utils.format_toman_full(verdict.baseline_median)} | "
                f"{verdict.label} | پیشنهاد: "
                f"{text_utils.format_toman_full(offer) if offer else 'پیشنهاد نمی‌شود'}"
            )
    return 0


def cmd_rules(args: argparse.Namespace) -> int:
    config = _load(args.config)
    if args.json:
        print(
            json.dumps(
                [{f.name: getattr(r, f.name) for f in fields(r)} for r in config.rules],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0
    print(f"قوانین فعال: {sum(1 for r in config.rules if r.enabled)} از {len(config.rules)}")
    for rule in config.rules:
        state = "فعال" if rule.enabled else "غیرفعال"
        print(f"  [{rule.id}] {rule.name} ({state})")
        for f in fields(rule):
            if f.name in {"id", "name", "enabled"}:
                continue
            value = getattr(rule, f.name)
            if value in (None, [], False):
                continue
            print(f"      {f.name}: {value}")
    return 0


def cmd_targets(args: argparse.Namespace) -> int:
    config = _load(args.config)
    cat = _catalog(config)
    print(f"اهداف: {len(config.targets)} (فعال: {len(config.active_targets)})")
    print(f"کاتالوگ: {len(cat)} خودرو | {len(cat.brands)} برند")
    for target in config.targets:
        state = "فعال" if target.enabled else "غیرفعال"
        terms = target_terms(cat, target)
        print(f"\n  [{target.id}] {target.label} ({state})")
        if target.paths:
            print(f"      paths: {', '.join(target.paths)}")
        if target.brand_paths:
            print(f"      brands: {', '.join(target.brand_paths)}")
        if target.query:
            print(f"      query: {target.query}")
        print(f"      Divar: {', '.join(terms) if terms else 'بدون فیلتر برند'}")
    return 0


def cmd_catalog(args: argparse.Namespace) -> int:
    config = _load(args.config)
    cat = _catalog(config)
    if not len(cat):
        print("کاتالوگ خالی است. فایل filters را بررسی کنید یا fetch را اجرا کنید.")
        return 1
    print(f"کاتالوگ: {len(cat)} خودرو | {len(cat.brands)} برند")
    if args.brand:
        model = cat.find(args.brand) or cat.match_in(args.brand)
        if not model:
            print(f"«{args.brand}» پیدا نشد")
            return 1
        print(f"  {model.name} → {model.path}  (عمق {model.depth})")
        return 0
    for brand in cat.brands[: args.limit]:
        count = len(cat.children_of(brand.path))
        print(f"  {brand.fa or brand.name:18} {brand.path:16} {count} مدل")
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    config = _load(args.config)
    with Database(config.db_path) as db:
        items = db.query(limit=100000)
        print(f"کل آگهی‌ها: {len(items)}")
        print(f"قیمت‌دار: {sum(1 for i in items if i.price.toman)}")
        print(f"مدل‌دار: {sum(1 for i in items if i.vehicle_path)}")
        print(f"سال‌دار: {sum(1 for i in items if i.year)}")
        print(f"کارکرد‌دار: {sum(1 for i in items if i.mileage_km)}")
        print(f"مغازه‌ای: {sum(1 for i in items if i.business)}")
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    config = _load(args.config)
    with Database(config.db_path) as db:
        removed = db.purge_older_than(config.storage.keep_days)
    print(f"{removed} آگهی قدیمی حذف شد")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    config = _load(args.config)
    notifier = _notifier(config)
    payload_path = Path(args.payload)
    if not payload_path.is_file():
        notifier.error(f"فایل پیدا نشد: {payload_path}")
        return 1
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    listings = parse_search_payload(payload, _catalog(config))
    notifier.banner(f"حالت دمو - {len(listings)} آگهی از {payload_path.name}")
    notifier.desktop = False

    def fetcher():
        yield listings

    with Database(config.db_path) as db:
        radar = Radar(config, notifier)
        try:
            report = radar.run_once(db, fetcher)
        except Exception as exc:  # noqa: BLE001
            notifier.error(f"اجرا ناموفق بود: {exc}")
            return 1
        notifier.summary(report.fetched, report.new_items, report.notified)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pride_radar", description="پایش خودکار آگهی‌های خودرو در دیوار"
    )
    parser.add_argument("--config", help="مسیر فایل تنظیمات (پیش‌فرض: ./config.json)")
    parser.add_argument("--version", action="version", version=f"pride-radar {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("web", help="رابط کاربری در مرورگر")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--no-browser", action="store_true")
    p.set_defaults(func=cmd_web)

    p = sub.add_parser("check", help="تست اتصال و نمایش آگهی‌های واقعی")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("fetch", help="دریافت یک‌باره آگهی‌ها")
    p.add_argument("--pages", type=int, default=None)
    p.add_argument("--no-specs", action="store_true", help="بدون باز کردن صفحهٔ جزئیات (سریع‌تر)")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("specs", help="دریافت مشخصات (رنگ، شاسی، گیربکس) برای آگهی‌های جدید")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_specs)

    p = sub.add_parser("bookmarks", help="لیست موارد بوکمارک‌شده")
    p.add_argument("--remove", help="حذف یک آگهی از بوکمارک با شناسه")
    p.set_defaults(func=cmd_bookmarks)

    p = sub.add_parser("watch", help="پایش مداوم و اعلان")
    p.add_argument("--pages", type=int, default=None)
    p.add_argument("--runs", type=int, default=None, help="۰ یعنی بی‌نهایت")
    p.set_defaults(func=cmd_watch)

    p = sub.add_parser("report", help="تحلیل بازار")
    p.add_argument("--days", type=int, default=30)
    p.add_argument("--suggest", type=int, default=0, metavar="N")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("price", help="برآورد قیمت برای شناسهٔ آگهی")
    p.add_argument("listing", nargs="+")
    p.set_defaults(func=cmd_price)

    p = sub.add_parser("rules", help="نمایش قوانین")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("targets", help="نمایش اهداف پایش")
    p.set_defaults(func=cmd_targets)

    p = sub.add_parser("catalog", help="کاتالوگ خودروها")
    p.add_argument("--brand", help="جست‌وجوی یک برند یا مدل")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_catalog)

    p = sub.add_parser("stats", help="آمار دیتابیس")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("clean", help="حذف آگهی‌های قدیمی")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("demo", help="اجرای زنجیره روی فایل نمونه")
    p.add_argument("payload")
    p.set_defaults(func=cmd_demo)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
