"""Market baselines and outlier-resistant statistics, grouped by vehicle."""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from .models import Listing

MAD_THRESHOLD = 5.0
MIN_SAMPLE_FOR_TRIM = 5
MIN_SAMPLE_FOR_BOUNDS = 4
PLAUSIBLE_LOW_RATIO = 0.25
PLAUSIBLE_HIGH_RATIO = 3.0
MIN_GROUP_FOR_YEAR = 5
MIN_GROUP_FOR_MODEL_YEAR = 3


@dataclass(slots=True, frozen=True)
class PriceStats:
    count: int
    minimum: int
    median: int
    mean: int
    maximum: int

    @property
    def spread(self) -> int:
        return self.maximum - self.minimum


EMPTY_STATS = PriceStats(0, 0, 0, 0, 0)


@dataclass(slots=True, frozen=True)
class MarketSnapshot:
    overall: PriceStats
    by_vehicle: dict[str, PriceStats]
    by_vehicle_year: dict[tuple[str, int], PriceStats]
    by_brand: dict[str, PriceStats]
    by_year: dict[int, PriceStats]
    sample_size: int

    def baseline_for(self, vehicle_path: str | None, year: int | None) -> PriceStats | None:
        if vehicle_path and year:
            exact = self.by_vehicle_year.get((vehicle_path, year))
            if exact and exact.count >= MIN_GROUP_FOR_MODEL_YEAR:
                return exact
        if vehicle_path:
            own = self.by_vehicle.get(vehicle_path)
            if own:
                return own
        if year:
            by_year = self.by_year.get(year)
            if by_year and by_year.count >= MIN_GROUP_FOR_YEAR:
                return by_year
        return self.overall if self.sample_size else None

    def stats_for(self, vehicle_path: str | None) -> PriceStats | None:
        if vehicle_path and vehicle_path in self.by_vehicle:
            return self.by_vehicle[vehicle_path]
        if vehicle_path:
            brand = vehicle_path.split("/")[0]
            if brand in self.by_brand:
                return self.by_brand[brand]
        return self.overall if self.sample_size else None


def plausibility_bounds(values: list[int]) -> tuple[float, float] | None:
    if len(values) < MIN_SAMPLE_FOR_BOUNDS:
        return None
    median = statistics.median(values)
    if median <= 0:
        return None
    return median * PLAUSIBLE_LOW_RATIO, median * PLAUSIBLE_HIGH_RATIO


def within_bounds(values: list[int], bounds: tuple[float, float] | None) -> list[int]:
    if not bounds:
        return values
    low, high = bounds
    kept = [value for value in values if low <= value <= high]
    return kept or values


def trim_mad(values: list[int]) -> list[int]:
    if len(values) < MIN_SAMPLE_FOR_TRIM:
        return values
    ordered = sorted(values)
    median = statistics.median(ordered)
    mad = statistics.median([abs(value - median) for value in ordered])
    if mad <= 0:
        return ordered
    bound = MAD_THRESHOLD * mad
    return [value for value in ordered if abs(value - median) <= bound]


def stats(values: Iterable[int]) -> PriceStats | None:
    data = trim_mad(list(values))
    if not data:
        return None
    return PriceStats(
        count=len(data),
        minimum=min(data),
        median=int(statistics.median(data)),
        mean=int(statistics.fmean(data)),
        maximum=max(data),
    )


def build_snapshot(listings: Iterable[Listing]) -> MarketSnapshot:
    priced = [item for item in listings if item.price.toman]
    prices = [item.price.toman for item in priced]
    bounds = plausibility_bounds(prices)

    vehicles: dict[str, list[int]] = {}
    vehicle_year: dict[tuple[str, int], list[int]] = {}
    brands: dict[str, list[int]] = {}
    years: dict[int, list[int]] = {}

    for item in priced:
        price = item.price.toman
        if item.vehicle_path:
            vehicles.setdefault(item.vehicle_path, []).append(price)
            if item.year:
                vehicle_year.setdefault((item.vehicle_path, item.year), []).append(price)
        if item.brand_path:
            brands.setdefault(item.brand_path, []).append(price)
        if item.year:
            years.setdefault(item.year, []).append(price)

    def summarize(values: list[int]) -> PriceStats | None:
        return stats(within_bounds(values, bounds))

    return MarketSnapshot(
        overall=summarize(prices) or EMPTY_STATS,
        by_vehicle={
            path: summary
            for path, values in vehicles.items()
            if (summary := summarize(values)) is not None
        },
        by_vehicle_year={
            key: summary
            for key, values in vehicle_year.items()
            if (summary := summarize(values)) is not None
        },
        by_brand={
            path: summary
            for path, values in brands.items()
            if (summary := summarize(values)) is not None
        },
        by_year={
            year: summary
            for year, values in years.items()
            if (summary := summarize(values)) is not None
        },
        sample_size=len(within_bounds(prices, bounds)),
    )


@dataclass(slots=True, frozen=True)
class Verdict:
    price_toman: int | None
    baseline_median: int | None
    delta_toman: int | None
    delta_ratio: float | None
    label: str

    @property
    def is_below_market(self) -> bool:
        return self.delta_ratio is not None and self.delta_ratio < 0


SUSPICIOUS_LABEL = "مشکوک به قیمت غیرواقعی"


def compare_to_market(
    price_toman: int | None,
    snapshot: MarketSnapshot,
    vehicle_path: str | None,
    year: int | None,
) -> Verdict:
    baseline = snapshot.baseline_for(vehicle_path, year)
    if price_toman is None or baseline is None or baseline.median <= 0:
        return Verdict(
            price_toman,
            baseline.median if baseline else None,
            None,
            None,
            "نامشخص",
        )

    delta = price_toman - baseline.median
    ratio = delta / baseline.median

    if ratio <= -0.5:
        label = SUSPICIOUS_LABEL
    elif ratio <= -0.15:
        label = "خیلی زیر بازار"
    elif ratio <= -0.07:
        label = "زیر بازار"
    elif ratio <= 0.07:
        label = "منصفانه"
    elif ratio <= 0.15:
        label = "کمی بالای بازار"
    else:
        label = "بالای بازار"

    return Verdict(price_toman, baseline.median, delta, ratio, label)


def suggest_offer(
    verdict: Verdict, floor_ratio: float = 0.88, cap_ratio: float = 0.97
) -> int | None:
    """Anchor an opening offer just under the market median, never above the ask."""
    if verdict.baseline_median is None or verdict.price_toman is None:
        return None
    if verdict.baseline_median <= 0 or verdict.label == SUSPICIOUS_LABEL:
        return None
    high = int(verdict.baseline_median * cap_ratio)
    low = int(verdict.baseline_median * floor_ratio)
    if verdict.price_toman <= high:
        return verdict.price_toman
    return max(low, high)


def profit_margin(price_toman: int | None, snapshot: MarketSnapshot,
                  vehicle_path: str | None, year: int | None) -> tuple[int | None, int | None]:
    """Return (estimated resale median, gross margin) for a buy-and-flip deal.

    The resale value is the market median for the same model and year; the margin
    is what you would gain by buying at the asking price and selling at median.
    Returns (None, None) when there is no usable baseline or the deal is implausible.
    """
    if price_toman is None or price_toman <= 0:
        return None, None
    baseline = snapshot.baseline_for(vehicle_path, year)
    if baseline is None or baseline.median <= 0:
        return None, None
    median = baseline.median
    if price_toman > median:
        return median, None
    ratio = price_toman / median
    if ratio <= 0.4:
        return median, None
    return median, median - price_toman


@dataclass(slots=True, frozen=True)
class FlipScore:
    margin_toman: int | None
    margin_ratio: float | None
    condition: float
    freshness: float
    total: float
    flags: tuple[str, ...]


def flip_score(
    listing: Any,
    snapshot: MarketSnapshot,
    *,
    reference_year: int = 1404,
    reference_mileage: int = 60_000,
) -> FlipScore:
    """Rank listings by flip potential: margin first, then condition and freshness."""
    median, margin = profit_margin(
        listing.price.toman, snapshot, listing.vehicle_path, listing.year
    )
    ratio = (margin / median) if (margin and median) else None
    flags: list[str] = []

    # ``paint`` is the colour, ``body_condition`` is the seller's body verdict.
    # Condition must be judged on the latter, otherwise every white car is
    # treated as repainted.
    body = (listing.body_condition or "").strip()
    chassis = (listing.chassis or "").strip()

    if chassis and chassis != "سالم":
        flags.append("شاسی مشکل‌دار")
    if body and body != "بی‌خط و خش":
        flags.append(f"بدنه {body}")
    if listing.business:
        flags.append("مغازه‌ای")

    # condition: clean body and sound chassis earn full marks
    condition = 1.0
    if body == "بی‌خط و خش":
        condition += 0
    elif body in ("رنگ‌شده", "تعویض‌شده", "کم‌رنگ"):
        condition -= 0.4
    if chassis == "سالم":
        pass
    elif chassis in ("آسیب‌دارد",):
        condition -= 0.5
    condition = max(0.0, min(1.0, condition))

    # freshness: newer year and lower mileage are better for resale
    age = max(0, reference_year - (listing.year or reference_year))
    freshness = 1.0 - min(1.0, age / 20.0)
    if listing.mileage_km is not None:
        freshness -= min(0.4, listing.mileage_km / 400_000)
    freshness = max(0.0, min(1.0, freshness))

    margin_score = 0.0
    if ratio is not None:
        margin_score = max(0.0, min(1.0, (ratio - 0.4) / 0.35))  # 40%->0, 75%->1
        if ratio >= 0.6:
            margin_score = min(1.0, margin_score + 0.1)

    total = 100.0 * (0.55 * margin_score + 0.25 * condition + 0.20 * freshness)
    return FlipScore(
        margin_toman=margin,
        margin_ratio=ratio,
        condition=condition,
        freshness=freshness,
        total=round(total, 1),
        flags=tuple(flags),
    )
