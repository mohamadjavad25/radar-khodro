"""Buying opportunities: what a car is really worth to resell, and the net profit.

The market median answers "what do sellers *ask* for a car like this". It does not
answer "what will *I* sell mine for". Those differ whenever the car you buy has
defects, high mileage, or you are competing against dealers.

So this module separates the two:

1. :func:`resale_estimate` starts from the median for the same model and year, then
   discounts it for the condition, mileage and engine state that the *listing*
   actually has. That is the realistic selling price.
2. :func:`evaluate_deal` subtracts the asking price to get net profit and ROI, and
   returns a plain verdict plus the reasons behind it.

One number drives the whole interface: ``profit_toman``. There is no separate
0-100 score competing for attention.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .market import MarketSnapshot
from .models import Listing, utcnow

# Verdict tiers, ordered by urgency.
BUY = "BUY"
WATCH = "WATCH"
PASS = "PASS"

# --- salvage / write-off detection ------------------------------------------
# Iranian used-car jargon. A car with any of these is not a flip: structural
# damage kills resale no matter how cheap the asking price looks.
# Matched against the title, the subtitle and the trim, all normalised to Persian
# letters with spaces, so spacing and Arabic/Persian ya/kaf variants do not matter.
SALVAGE_MARKERS = (
    "تصادف",
    "تصادفی",
    "ورق کرده",
    "ورق‌کرده",
    "اوت گاز",
    "اوراق",
    "اسقاط",
    "چکشی",
    "سوخته",
    "آتش سوزی",
    "اتش سوزی",
    "سیل",
    "زلزله",
    "بلای طبیعی",
    "شاسی ضربه",
    "شاسی ستون",
    "شاسی دوگانه",
    "شاسی مشکل",
    "مالک کف",
    "کف مالک",
    "نیاز به تعمیر اساسی",
    "مونتاژ مجدد",
    "موتور خراب",
    "گیربکس خراب",
    "موتور ندارد",
    "بدون موتور",
    "بدنه ندارد",
    "بدون بدنه",
)
# Cheap cars are also often "قیمت توافقی" shells or part-payments; not a flip.
RED_FLAG_PRICE_WORDS = ("قیمت توافقی", "مذاکره ای", "تومان توافقی")

# --- resale discounts -------------------------------------------------------
# Applied multiplicatively to the market median. Deliberately conservative:
# over-promising profit is how a flip ends in a loss.
CLEAN_BODY = 1.0
UNKNOWN_BODY = 0.95
# "no paintwork" as far as resale is concerned. Cosmetic scratches do not move the
# price; a repaint does.
NO_PAINTWORK = ("بی‌خط و خش", "سالم", "خط و خش جزیی")
BODY_FACTORS = {
    "بی‌خط و خش": 1.0,
    "سالم": 1.0,
    "خط و خش جزیی": 0.99,
    "کم‌رنگ": 0.95,
    "رنگ‌شده": 0.92,
    "تعویض‌شده": 0.88,
}
CHASSIS_FACTORS = {
    "سالم": 1.0,
    "سالم و پلمپ": 1.0,
}
CHASSIS_DAMAGED_FACTOR = 0.85  # structural damage is the hardest defect to sell around
ENGINE_FACTORS = {"سالم": 1.0, "سالم و پلمپ": 1.0}
ENGINE_BAD_FACTOR = 0.93
GEARBOX_BAD_FACTOR = 0.96

# mileage: free up to the reference, then -1.5% per 10k km, floored
MILEAGE_FREE_KM = 60_000
MILEAGE_STEP_KM = 10_000
MILEAGE_STEP_PENALTY = 0.015
MILEAGE_FLOOR = 0.85

# A price far below median is almost always a teaser, a swap, or something wrong.
# Sellers quote half of market to attract calls; you do not resell at median.
PRICE_FLOOR_RATIO = 0.55
# You will not sell instantly at the median asking price: you must price under it
# to move the car, and it takes time. This is what you actually bank.
SELL_THROUGH_RATIO = 0.93
# "well under the clean median" is the signal we surface as a reason
UNDER_MARKET_RATIO = 0.80

AGE_BONUS_HOURS = 6.0
AGE_BONUS_MAX = 0.05


@dataclass(slots=True)
class DealSettings:
    """Hard requirements the user actually cares about."""

    min_profit_toman: int = 30_000_000
    min_roi: float = 0.06
    max_age_hours: int = 24
    require_clean_body: bool = True
    require_sound_chassis: bool = True
    require_known_condition: bool = True
    exclude_business: bool = True
    max_mileage_km: int | None = None
    min_year: int | None = None
    max_year: int | None = None
    max_price_toman: int | None = None
    exclude_salvage: bool = True


@dataclass(slots=True)
class Deal:
    """One priced, condition-aware buying opportunity."""

    listing_id: str
    verdict: str
    profit_toman: int | None
    roi: float | None
    resale_toman: int | None
    discount_factor: float
    age_hours: float | None
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def is_buy(self) -> bool:
        return self.verdict == BUY

    @property
    def is_tradeable(self) -> bool:
        return self.verdict in (BUY, WATCH)


@dataclass(slots=True)
class DealResult:
    """Everything the interface needs about one car, in one object."""

    deal: Deal
    listing: Listing
    good: tuple[str, ...] = ()
    bad: tuple[str, ...] = ()
    detail: dict[str, str] = field(default_factory=dict)


def _search_text(listing: Listing) -> str:
    """Normalised haystack for keyword checks: title, description and trim."""
    from . import text as text_utils

    raw = " ".join(
        part for part in (listing.title, listing.description, listing.trim) if part
    )
    return text_utils.normalize(raw)


def salvage_reasons(listing: Listing) -> list[str]:
    """Damage/shortage markers found in the ad text. Empty list = nothing found."""
    haystack = _search_text(listing)
    if not haystack:
        return []
    found: list[str] = []
    for marker in SALVAGE_MARKERS:
        if marker in haystack:
            label = marker
            if marker == "موتور ندارد":
                label = "بدون موتور"
            elif marker == "بدنه ندارد":
                label = "بدون بدنه"
            if label not in found:
                found.append(label)
    for word in RED_FLAG_PRICE_WORDS:
        if word in haystack:
            found.append(f"{word} — قیمت واقعی نیست")
    return found


def _factor(mapping: dict[str, float], value: str | None, default: float) -> float:
    if value is None:
        return default
    return mapping.get(value.strip(), default)


def condition_discount(listing: Listing) -> tuple[float, list[str], list[str]]:
    """Total resale discount for this listing's known defects.

    Returns (factor, good_tags, bad_tags) where factor is <= 1.0.
    """
    good: list[str] = []
    bad: list[str] = []
    factor = 1.0

    body = (listing.body_condition or "").strip()
    if not body:
        factor *= UNKNOWN_BODY
        bad.append("وضعیت بدنه نامشخص")
    else:
        factor *= _factor(BODY_FACTORS, body, 0.93)
        if body in NO_PAINTWORK:
            good.append("بدنه بدون رنگ")
        else:
            bad.append(f"بدنه {body}")

    chassis = (listing.chassis or "").strip()
    if chassis:
        if chassis in ("سالم", "سالم و پلمپ"):
            good.append("شاسی سالم")
        elif chassis == "نامشخص":
            factor *= 0.97
            bad.append("شاسی نامشخص")
        else:
            factor *= CHASSIS_DAMAGED_FACTOR
            bad.append(f"شاسی {chassis}")

    engine = (listing.engine or "").strip()
    if engine:
        if engine in ENGINE_FACTORS:
            good.append("موتور سالم")
        else:
            factor *= ENGINE_BAD_FACTOR
            bad.append(f"موتور {engine}")

    gearbox = (listing.gearbox_state or "").strip()
    if gearbox and gearbox not in ("سالم", "دستی", "اتومات", "نیمه‌اتومات", "نامشخص"):
        factor *= GEARBOX_BAD_FACTOR
        bad.append(f"گیربکس {gearbox}")

    if listing.business:
        factor *= 0.93
        bad.append("آگهی مغازه‌ای")

    damage = salvage_reasons(listing)
    if damage:
        factor *= 0.75  # structural damage is not resellable at a normal margin
        for marker in damage:
            bad.append(marker)

    # mileage, measured against a clean reference
    km = listing.mileage_km
    if km is not None:
        if km <= MILEAGE_FREE_KM:
            good.append("کم‌کارکرد")
        else:
            steps = (km - MILEAGE_FREE_KM) / MILEAGE_STEP_KM
            penalty = min(1 - MILEAGE_FLOOR, steps * MILEAGE_STEP_PENALTY)
            factor *= 1 - penalty
            bad.append(f"{km:,} کیلومتر")

    return factor, good, bad


def resale_estimate(
    listing: Listing,
    snapshot: MarketSnapshot,
    *,
    sample_size: int | None = None,
) -> tuple[int | None, float]:
    """Realistic selling price for *this* car, and the discount factor used.

    Two discounts stack: the condition of this specific car, and the fact that you
    must price below the median asking price to actually sell it.
    """
    baseline = snapshot.baseline_for(listing.vehicle_path, listing.year)
    if baseline is None or baseline.median <= 0:
        return None, 1.0
    factor, _good, _bad = condition_discount(listing)
    return int(baseline.median * factor * SELL_THROUGH_RATIO), factor


def _age_hours(listing: Listing) -> float | None:
    stamp = listing.published_at or listing.created_at
    if stamp is None:
        return None
    return max(0.0, (utcnow() - stamp).total_seconds() / 3600)


def evaluate_deal(
    listing: Listing,
    snapshot: MarketSnapshot,
    settings: DealSettings,
    *,
    sample_size: int | None = None,
) -> DealResult:
    """Price one listing and decide whether it is worth the user's time."""
    factor, good, bad = condition_discount(listing)
    resale, _factor = resale_estimate(listing, snapshot)
    price = listing.price.toman
    age = _age_hours(listing)

    blockers: list[str] = []
    reasons: list[str] = []
    # facts the seller never declared: worth a phone call, never a confident buy
    uncertain: list[str] = []

    # --- hard requirements (cheapest checks first) -------------------------
    if settings.exclude_salvage:
        for marker in salvage_reasons(listing):
            blockers.append(f"آسیب‌دیده: {marker}")

    if settings.exclude_business and listing.business:
        blockers.append("آگهی مغازه‌ای")

    if settings.min_year and (listing.year is None or listing.year < settings.min_year):
        blockers.append(f"زیر {settings.min_year}")
    if settings.max_year and (listing.year is None or listing.year > settings.max_year):
        blockers.append(f"بالای {settings.max_year}")
    if (
        settings.max_mileage_km
        and listing.mileage_km is not None
        and listing.mileage_km > settings.max_mileage_km
    ):
        blockers.append("کارکرد زیاد")
    if (
        settings.max_price_toman
        and price is not None
        and price > settings.max_price_toman
    ):
        blockers.append("بالاتر از سقف قیمت")

    if age is not None:
        if age > settings.max_age_hours:
            blockers.append(f"{int(age)} ساعت قدیمی")
        elif age <= AGE_BONUS_HOURS:
            reasons.append("همین حالا منتشر شده")
    else:
        blockers.append("زمان انتشار نامشخص")

    if not listing.specs_fetched:
        if settings.require_known_condition:
            blockers.append("مشخصات بررسی نشده")
        else:
            reasons.append("مشخصات نامشخص — ریسک")

    body = (listing.body_condition or "").strip()
    if settings.require_clean_body:
        if body and body not in NO_PAINTWORK:
            blockers.append(f"بدنه {body}")
        elif not body:
            # Seller did not declare paintwork. Not a rejection: you call and ask.
            # But it can never be a confident BUY.
            uncertain.append("وضعیت بدنه اعلام نشده — بپرس")

    chassis = (listing.chassis or "").strip()
    if settings.require_sound_chassis:
        if chassis and chassis != "سالم":
            blockers.append(f"شاسی {chassis}")
        elif not chassis:
            uncertain.append("وضعیت شاسی اعلام نشده — بپرس")

    # --- economics ---------------------------------------------------------
    profit: int | None = None
    roi: float | None = None
    if price is None or price <= 0:
        blockers.append("قیمت مشخص نیست")
    elif resale is None:
        blockers.append("مبنای بازار ندارد")
    else:
        ratio = price / resale
        if ratio < PRICE_FLOOR_RATIO:
            blockers.append("قیمت خیلی پایین — طعمه یا معاوضه")
        elif ratio >= 1.0:
            blockers.append("گران‌تر از ارزش واقعی")
        else:
            profit = resale - price
            roi = profit / price
            if ratio <= UNDER_MARKET_RATIO:
                reasons.append("زیر قیمت بازار")
            if profit >= settings.min_profit_toman:
                reasons.append(f"سود {profit:,} تومان")
            if roi is not None and roi >= settings.min_roi:
                reasons.append(f"{roi * 100:.0f}٪ بازده")

    if age is not None and age <= AGE_BONUS_HOURS and not any(
        "منتشر شده" in r for r in reasons
    ):
        reasons.append("تازه")

    detail: dict[str, str] = {}
    baseline = snapshot.baseline_for(listing.vehicle_path, listing.year)
    if baseline is not None:
        detail["میانهٔ بازار"] = f"{baseline.median:,} تومان"
    if resale is not None:
        detail["قیمت واقعی فروش"] = f"{resale:,} تومان"
    if price:
        detail["قیمت آگهی"] = f"{price:,} تومان"
    if factor < 0.999:
        detail["افت بابت ایراد"] = f"{int(round((1 - factor) * 100))}٪"

    # --- verdict -----------------------------------------------------------
    meets_floor = (
        profit is not None
        and profit >= settings.min_profit_toman
        and (roi is None or roi >= settings.min_roi)
    )
    if blockers:
        verdict = PASS
    elif uncertain:
        # profitable on paper but resting on facts nobody stated: verify first
        verdict = WATCH
    elif meets_floor:
        verdict = BUY
    elif profit is not None and profit > 0:
        verdict = WATCH
    else:
        verdict = PASS
        blockers.append("سود ندارد")

    return DealResult(
        deal=Deal(
            listing_id=listing.listing_id,
            verdict=verdict,
            profit_toman=profit,
            roi=roi,
            resale_toman=resale,
            discount_factor=round(factor, 4),
            age_hours=round(age, 1) if age is not None else None,
            reasons=tuple(dict.fromkeys(reasons + uncertain)),
            blockers=tuple(dict.fromkeys(blockers)),
        ),
        listing=listing,
        good=tuple(good),
        bad=tuple(bad),
        detail=detail,
    )


def _rank(result: DealResult) -> tuple:
    deal = result.deal
    profit = deal.profit_toman or 0
    return (
        0 if deal.verdict == BUY else 1 if deal.verdict == WATCH else 2,
        -profit,
        deal.age_hours if deal.age_hours is not None else 1e9,
    )


def rank_results(
    results: list[DealResult], *, include_pass: bool = False
) -> list[DealResult]:
    """Order by verdict first (buys before watches), then profit, then freshness."""
    ordered = sorted(results, key=_rank)
    if include_pass:
        return ordered
    return [r for r in ordered if r.deal.verdict != PASS]


def find_opportunities(
    listings: list[Listing],
    snapshot: MarketSnapshot,
    settings: DealSettings,
    *,
    include_pass: bool = False,
) -> list[DealResult]:
    """Evaluate and rank in one call."""
    results = [evaluate_deal(item, snapshot, settings) for item in listings]
    return rank_results(results, include_pass=include_pass)


def summarize(results: list[DealResult]) -> dict[str, int]:
    buys = sum(1 for r in results if r.deal.verdict == BUY)
    watch = sum(1 for r in results if r.deal.verdict == WATCH)
    best = max((r.deal.profit_toman or 0 for r in results), default=0)
    return {
        "considered": len(results),
        "buy": buys,
        "watch": watch,
        "pass": len(results) - buys - watch,
        "best_profit": best,
    }
