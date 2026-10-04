"""Rule matching and listing scoring."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Rule
from .market import SUSPICIOUS_LABEL, MarketSnapshot, Verdict, compare_to_market
from .models import Listing


@dataclass(slots=True, frozen=True)
class MatchResult:
    listing: Listing
    rule: Rule
    score: float
    verdict: Verdict

    @property
    def is_hit(self) -> bool:
        return True


@dataclass(slots=True, frozen=True)
class RejectReason:
    rule_id: str
    reason: str


@dataclass(slots=True, frozen=True)
class Evaluation:
    matched: list[MatchResult]
    rejected: list[RejectReason]


def _fail(rule_id: str, reason: str) -> RejectReason:
    return RejectReason(rule_id, reason)


def _path_matches(listing: Listing, wanted: list[str]) -> bool:
    if not wanted:
        return True
    path = (listing.vehicle_path or "").strip("/").lower()
    for want in wanted:
        want = want.strip("/").lower()
        if not want:
            continue
        if path == want:
            return True
        if path.startswith(want + "/"):
            return True
        if "/" not in want and (listing.brand_path or "").lower() == want:
            return True
    return False


def check_rule(
    listing: Listing, rule: Rule, snapshot: MarketSnapshot
) -> tuple[Verdict | None, RejectReason | None]:
    if not _path_matches(listing, rule.paths):
        return None, _fail(rule.id, "مدل مطابقت ندارد")
    if rule.min_year and (listing.year is None or listing.year < rule.min_year):
        return None, _fail(rule.id, f"سال زیر {rule.min_year}")
    if rule.max_year and (listing.year is None or listing.year > rule.max_year):
        return None, _fail(rule.id, f"سال بالای {rule.max_year}")
    if rule.max_mileage_km:
        if listing.mileage_km is None or listing.mileage_km > rule.max_mileage_km:
            return None, _fail(rule.id, "کارکرد زیاد یا نامشخص")
    if rule.exclude_business and listing.business:
        return None, _fail(rule.id, "آگهی مغازه‌ای")

    price = listing.price.toman
    if rule.require_price and price is None:
        return None, _fail(rule.id, "قیمت مشخص نیست")
    if rule.min_price_toman and (price is None or price < rule.min_price_toman):
        return None, _fail(rule.id, f"قیمت زیر {rule.min_price_toman:,}")
    if rule.max_price_toman and (price is None or price > rule.max_price_toman):
        return None, _fail(rule.id, f"قیمت بالای {rule.max_price_toman:,}")

    verdict = compare_to_market(price, snapshot, listing.vehicle_path, listing.year)
    if rule.exclude_suspicious and verdict.label == SUSPICIOUS_LABEL:
        return None, _fail(rule.id, "قیمت غیرواقعی")
    if rule.max_discount_vs_market is not None:
        ratio = verdict.delta_ratio
        if ratio is None:
            return None, _fail(rule.id, "مبنای بازار محاسبه نشد")
        if ratio > -rule.max_discount_vs_market:
            return None, _fail(rule.id, "اختلاف قیمت با بازار کافی نیست")

    return verdict, None


def score_listing(
    listing: Listing, snapshot: MarketSnapshot, weights: dict[str, float]
) -> float:
    total = sum(weights.values()) or 1.0
    score = 0.0

    verdict = compare_to_market(
        listing.price.toman, snapshot, listing.vehicle_path, listing.year
    )
    if verdict.delta_ratio is not None:
        score += weights.get("price", 0.0) * max(0.0, min(1.0, (1 - verdict.delta_ratio) / 0.4))

    if listing.year:
        score += weights.get("year", 0.0) * min(1.0, listing.year / 1404)

    if listing.mileage_km is not None:
        freshness = 1.0 - min(1.0, listing.mileage_km / 250_000)
        score += weights.get("mileage", 0.0) * freshness

    if listing.vehicle_path:
        depth = len(listing.vehicle_path.split("/"))
        score += weights.get("model", 0.0) * (1.0 if depth >= 2 else 0.5)

    if listing.business:
        score *= 0.6
    if listing.price.negotiable and listing.price.toman is None:
        score *= 0.5

    return round(100.0 * score / total, 1)


def evaluate(
    listing: Listing, rules: list[Rule], snapshot: MarketSnapshot, weights: dict[str, float]
) -> Evaluation:
    matched: list[MatchResult] = []
    rejected: list[RejectReason] = []
    score = score_listing(listing, snapshot, weights)

    for rule in rules:
        if not rule.enabled:
            continue
        verdict, reason = check_rule(listing, rule, snapshot)
        if verdict is None or reason is not None:
            if reason:
                rejected.append(reason)
            continue
        matched.append(MatchResult(listing, rule, score, verdict))

    return Evaluation(matched, rejected)


def passes_global_filters(listing: Listing, filters: object) -> RejectReason | None:
    if getattr(filters, "min_year", None) and (
        listing.year is None or listing.year < filters.min_year
    ):
        return RejectReason("filters", f"سال زیر {filters.min_year}")
    if getattr(filters, "max_year", None) and (
        listing.year is None or listing.year > filters.max_year
    ):
        return RejectReason("filters", f"سال بالای {filters.max_year}")
    if getattr(filters, "max_mileage_km", None) and (
        listing.mileage_km is None or listing.mileage_km > filters.max_mileage_km
    ):
        return RejectReason("filters", "کارکرد زیاد یا نامشخص")
    if getattr(filters, "min_price_toman", None) and (
        listing.price.toman is None or listing.price.toman < filters.min_price_toman
    ):
        return RejectReason("filters", "زیر حداقل قیمت")
    if getattr(filters, "max_price_toman", None) and (
        listing.price.toman is None or listing.price.toman > filters.max_price_toman
    ):
        return RejectReason("filters", "بالای حداکثر قیمت")
    if getattr(filters, "exclude_business", False) and listing.business:
        return RejectReason("filters", "آگهی مغازه‌ای")
    return None
