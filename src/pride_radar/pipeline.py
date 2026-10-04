"""Run orchestration: fetch, persist, score, notify."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from . import rules as rules_engine
from .config import Config, VehicleTarget
from .db import Database
from .market import MarketSnapshot, build_snapshot
from .models import Listing, utcnow
from .notify import Notifier


@dataclass(slots=True)
class RunReport:
    fetched: int = 0
    stored: int = 0
    new_items: int = 0
    price_changes: int = 0
    scored: int = 0
    notified: int = 0
    suppressed: int = 0
    rejected_by_filters: int = 0
    market_size: int = 0
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None
    errors: list[str] = field(default_factory=list)

    def duration_seconds(self) -> float:
        end = self.finished_at or utcnow()
        return (end - self.started_at).total_seconds()


class Radar:
    def __init__(self, config: Config, notifier: Notifier) -> None:
        self.config = config
        self.notifier = notifier

    def snapshot(self, db: Database) -> MarketSnapshot:
        cutoff = utcnow() - timedelta(hours=self.config.scoring.market_max_age_hours)
        recent = db.query(limit=6000)
        fresh = [item for item in recent if item.last_seen_at >= cutoff]
        return build_snapshot(fresh or recent)

    def ingest(self, db: Database, listings: list[Listing]) -> RunReport:
        report = RunReport()
        results = db.upsert_many(listings)
        report.fetched = len(listings)
        report.stored = len(results)
        report.new_items = sum(1 for item in results if item.is_new)
        report.price_changes = sum(1 for item in results if item.price_changed)
        return report

    def evaluate(self, db: Database, report: RunReport) -> list[rules_engine.MatchResult]:
        from .market import profit_margin

        snapshot = self.snapshot(db)
        report.market_size = snapshot.sample_size
        weights = self.config.scoring.weights

        pool = db.query(limit=4000, dismissed=False)
        scores: dict[str, float] = {}
        margins: dict[str, int] = {}
        hits: list[rules_engine.MatchResult] = []

        for listing in pool:
            if rules_engine.passes_global_filters(listing, self.config.filters):
                report.rejected_by_filters += 1
                continue

            _, margin = profit_margin(
                listing.price.toman, snapshot, listing.vehicle_path, listing.year
            )
            if margin is not None:
                listing.margin_toman = margin
                margins[listing.listing_id] = margin

            evaluation = rules_engine.evaluate(listing, self.config.rules, snapshot, weights)
            scores[listing.listing_id] = rules_engine.score_listing(listing, snapshot, weights)
            for match in evaluation.matched:
                if not match.rule.notify:
                    continue
                if db.already_notified(listing.listing_id, match.rule.id):
                    report.suppressed += 1
                    continue
                hits.append(match)

        db.set_scores(scores)
        db.set_margin(margins)
        report.scored = len(scores)
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits

    def notify(self, db: Database, hits: list[rules_engine.MatchResult], report: RunReport) -> None:
        for match in hits:
            self.notifier.hit(match)
            db.mark_notified(match.listing.listing_id, match.rule.id, match.score)
            report.notified += 1
            time.sleep(0.1)
        if not hits:
            self.notifier.info("مورد جدیدی مطابق قوانین پیدا نشد.")

    def run_once(self, db: Database, fetcher_factory: Any) -> RunReport:
        run_id = db.start_run()
        report = RunReport()
        try:
            for batch in fetcher_factory():
                report.fetched += len(batch)
                ingested = self.ingest(db, batch)
                report.stored += ingested.stored
                report.new_items += ingested.new_items
                report.price_changes += ingested.price_changes
            hits = self.evaluate(db, report)
            self.notify(db, hits, report)
            report.finished_at = utcnow()
            db.finish_run(
                run_id,
                fetched=report.fetched,
                new_items=report.new_items,
                notified=report.notified,
                status="ok",
            )
            return report
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{type(exc).__name__}: {exc}")
            report.finished_at = utcnow()
            db.finish_run(
                run_id,
                fetched=report.fetched,
                new_items=report.new_items,
                notified=report.notified,
                status="error",
                message=report.errors[-1],
            )
            raise

    def watch(self, db: Database, fetcher_factory: Any) -> list[RunReport]:
        reports: list[RunReport] = []
        interval = self.config.watch.interval_minutes
        max_runs = self.config.watch.max_runs
        names = "، ".join(t.label for t in self.config.active_targets) or "همهٔ خودروها"

        self.notifier.banner(f"پایش زنده - هر {interval} دقیقه | {self.config.search.city} | {names}")
        if max_runs:
            self.notifier.info(f"تعداد اجرا محدود به {max_runs} بار است.")

        run_number = 0
        while max_runs == 0 or run_number < max_runs:
            run_number += 1
            self.notifier.banner(f"اجرای {run_number}" + (f" از {max_runs}" if max_runs else ""))
            try:
                report = self.run_once(db, fetcher_factory)
                reports.append(report)
                self.notifier.summary(report.fetched, report.new_items, report.notified)
            except KeyboardInterrupt:
                self.notifier.warn("توقف با Ctrl+C")
                break
            except Exception as exc:  # noqa: BLE001
                self.notifier.error(f"اجرا ناموفق بود: {exc}")

            if max_runs and run_number >= max_runs:
                break
            sleep_for = interval * 60
            self.notifier.info(f"خواب {sleep_for // 60} دقیقه ...")
            time.sleep(sleep_for)

        return reports


def random_pause(config: Config) -> None:
    time.sleep(random.uniform(config.watch.delay_min_seconds, config.watch.delay_max_seconds))


def target_terms(catalog: Any, target: VehicleTarget) -> list[str]:
    """Resolve a target to the Divar display names for the brand_model filter.

    Only the model names are sent. Adding the parent brand used to widen the
    result set to the whole brand, because Divar treats repeated_string as OR.
    """
    if target.is_all or catalog is None or not len(catalog):
        return []
    terms: list[str] = []
    for path in target.brand_paths:
        brand = catalog.resolve_path(path)
        if brand is not None:
            terms.append(brand.name)
    for path in target.paths:
        model = catalog.resolve_path(path)
        if model is not None:
            terms.append(model.name)
    return list(dict.fromkeys(terms))


def listing_in_target(listing: Listing, target: VehicleTarget) -> bool:
    """Authoritative membership test for a fetched listing.

    The v8 ``brand_model`` filter is not honoured by the current Divar backend:
    a search for ``Pride 131`` still returns vans, pickups and other Pride
    variants. We therefore scope results ourselves against the resolved catalog
    path, which every listing carries.
    """
    if target.is_all:
        return True
    path = (listing.vehicle_path or "").lower()
    brand = (listing.brand_path or "").lower()
    for wanted in target.paths:
        wanted = wanted.lower()
        if path == wanted or path.startswith(wanted + "/"):
            return True
    for wanted in target.brand_paths:
        wanted = wanted.lower()
        if brand == wanted or path == wanted or path.startswith(wanted + "/"):
            return True
    return False


def scope_listings(
    listings: list[Listing], target: VehicleTarget
) -> tuple[list[Listing], int]:
    """Keep only the listings that belong to ``target``; report how many were cut."""
    if target.is_all:
        return listings, 0
    kept = [item for item in listings if listing_in_target(item, target)]
    return kept, len(listings) - len(kept)
