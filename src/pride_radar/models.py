"""Core domain models and generic vehicle text extraction."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import catalog as catalog_mod
from . import text as text_utils


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iran_year_to_gregorian(iran_year: int) -> int:
    return iran_year + 621


def gregorian_to_iran_year(year: int) -> int:
    return year - 621


@dataclass(slots=True)
class Price:
    toman: int | None = None
    negotiable: bool = False
    raw: str = ""

    @property
    def is_known(self) -> bool:
        return self.toman is not None


@dataclass(slots=True)
class Listing:
    listing_id: str
    title: str = ""
    description: str = ""
    price: Price = field(default_factory=Price)
    vehicle_path: str | None = None      # canonical catalog path, e.g. "pride/131"
    vehicle_name: str | None = None     # catalog name, e.g. "Pride 131"
    vehicle_fa: str | None = None        # Persian label, e.g. "پراید ۱۳۱"
    brand_path: str | None = None
    brand_fa: str | None = None
    year: int | None = None
    mileage_km: int | None = None
    city: str | None = None
    district: str | None = None
    url: str | None = None
    business: bool = False
    published_at: datetime | None = None
    published_text: str | None = None
    paint: str | None = None
    chassis: str | None = None
    body_condition: str | None = None
    engine: str | None = None
    gearbox: str | None = None
    gearbox_state: str | None = None
    fuel: str | None = None
    insurance: str | None = None
    trim: str | None = None
    body_type: str | None = None
    specs_fetched: bool = False
    bookmarked: bool = False
    dismissed: bool = False
    noted_at: datetime | None = None
    created_at: datetime | None = None
    first_seen_at: datetime = field(default_factory=utcnow)
    last_seen_at: datetime = field(default_factory=utcnow)
    score: float | None = None
    margin_toman: int | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return self.vehicle_fa or self.vehicle_name or self.title or "خودرو"

    @property
    def brand_label(self) -> str:
        return self.brand_fa or self.vehicle_name or self.label

    @property
    def gregorian_year(self) -> int | None:
        return iran_year_to_gregorian(self.year) if self.year else None

    @property
    def slug(self) -> str:
        return (self.vehicle_path or "unknown").replace("/", "-")

    @property
    def published_iso(self) -> str | None:
        return self.published_at.isoformat() if self.published_at else None

    def haystack(self) -> str:
        return text_utils.normalize(
            " ".join(filter(None, [self.title, self.description, self.district]))
        )


# ---------------------------------------------------------------- year / mileage

_MILEAGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<!\d)(\d[\d,]*)\s*هزار\s*کیلومتر"),
    re.compile(r"کارکرد\s*[:：]?\s*(\d[\d,]*)"),
    re.compile(r"(?<!\d)(\d[\d,]*)\s*کیلومتر"),
    re.compile(r"(?<!\d)(\d[\d,]*)\s*هزار\b"),
    re.compile(r"(?<!\d)(\d[\d,]*)\s*k\s*m\b", re.IGNORECASE),
)
_MILEAGE_MASK_RE = re.compile("|".join(f"(?:{p.pattern})" for p in _MILEAGE_PATTERNS))

_YEAR_BOUNDS = (1360, 1412)
_MODEL_TOKEN_RE = re.compile(r"(?<!\d)(\d{2,4}[a-z]?)(?!\d)", re.IGNORECASE)
# "۱۱ ساعت پیش" is an age, not a model year. Masking relative-time expressions
# stops the 2-digit fallback below from turning "11 hours ago" into the year 1411.
_REL_TIME_MASK_RE = re.compile(
    r"(?<!\d)\d{1,3}\s*(?:دقیقه|ساعت|روز|هفته|ماه|سال)\s*(?:پیش|قبل)"
    r"|\d{4}\s*[-–]\s*\d{4}"
    r"|\b\d{1,3}\s*(?:هزار|میلیون)\b"
)


def _mask_mileage(blob: str) -> str:
    return _REL_TIME_MASK_RE.sub(" ", _MILEAGE_MASK_RE.sub(" ", blob))


def _current_persian_year() -> int:
    """Upper bound for a model year: this year, never beyond it."""
    g = utcnow().year
    return g - 621 if g % 33 >= 1 else g - 622


def _coerce_year(token: str) -> int | None:
    digits = re.sub(r"\D", "", token)
    if not digits:
        return None
    value = int(digits)
    if len(digits) == 2:
        value += 1300 if value >= 70 else 1400
    low, high = _YEAR_BOUNDS
    # a model year can be the current year at most
    high = min(high, _current_persian_year())
    return value if low <= value <= high else None


def extract_year(*sources: str | None) -> int | None:
    blob = text_utils.normalize_digits(" ".join(s for s in sources if s))
    if not blob:
        return None
    masked = _mask_mileage(blob)

    labelled = re.search(r"(?:سال|مدل|ساخت)\s*:?\s*(\d{2,4})", masked)
    if labelled:
        candidate = _coerce_year(labelled.group(1))
        if candidate:
            return candidate

    for pattern in (r"(?<!\d)(\d{4})(?!\d)", r"(?<!\d)(\d{2})(?!\d)"):
        for match in re.finditer(pattern, masked):
            candidate = _coerce_year(match.group(1))
            if candidate:
                return candidate
    return None


def extract_mileage(*sources: str | None) -> int | None:
    blob = text_utils.normalize_digits(" ".join(s for s in sources if s))
    if not blob:
        return None
    for pattern in _MILEAGE_PATTERNS:
        match = pattern.search(blob)
        if not match:
            continue
        raw = int(match.group(1).replace(",", ""))
        if raw <= 0:
            continue
        has_thousand = "هزار" in match.group(0) or "k" in match.group(0).lower()
        if has_thousand and raw < 1000:
            raw *= 1000
        if raw <= 1_500_000:
            return raw
    return None


# ---------------------------------------------------------------- vehicle match


@dataclass(slots=True, frozen=True)
class VehicleMatch:
    path: str | None
    name: str | None
    fa: str | None
    brand_path: str | None
    brand_fa: str | None
    depth: int


NO_MATCH = VehicleMatch(None, None, None, None, None, 0)


def match_vehicle(catalog: Any, *sources: str | None) -> VehicleMatch:
    """Identify the vehicle a listing refers to using the catalog."""
    if catalog is None or not len(catalog):
        return NO_MATCH
    blob = text_utils.normalize_digits(" ".join(s for s in sources if s))
    if not blob:
        return NO_MATCH
    model = catalog.match_in(blob)
    if model is None:
        return NO_MATCH
    return _match_from_model(catalog, model)


def _match_from_model(catalog: Any, model: Any) -> VehicleMatch:
    path = model.path
    depth = model.depth
    # A trim path (pride/131/le) is reported as its model (pride/131).
    if depth >= 3:
        parent = "/".join(path.split("/")[:2])
        parent_model = catalog.resolve_path(parent)
        if parent_model is not None:
            model = parent_model
            path = parent
            depth = 2

    brand = catalog.resolve_path(model.brand_path)
    brand_fa = brand.fa if brand is not None else None
    if not brand_fa:
        brand_fa = catalog_mod.BRAND_FA.get(model.brand_path)

    name = model.name
    fa = model.fa
    if not fa and depth == 2:
        slug = path.split("/")[-1]
        brand_part = brand_fa or ""
        if brand_part and slug:
            fa = f"{brand_part} {slug}"
    return VehicleMatch(
        path=path,
        name=name,
        fa=fa or name,
        brand_path=model.brand_path,
        brand_fa=brand_fa,
        depth=depth,
    )


def matches_target(match: VehicleMatch, targets: list[str]) -> bool:
    """True when a matched vehicle belongs to any of the target paths/brands."""
    if not targets:
        return True
    if match.path is None:
        return False
    wanted = {t.strip("/").lower() for t in targets if t}
    if not wanted:
        return True
    if match.path in wanted:
        return True
    brand = match.brand_path or ""
    if brand and brand in wanted:
        return True
    return any(
        match.path.startswith(w + "/") for w in wanted if "/" not in w
    )


def is_vehicle_listing(match: VehicleMatch) -> bool:
    return match.path is not None
