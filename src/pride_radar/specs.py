"""Car specs scraped from a Divar post page.

The search list only carries title/price/mileage, so condition data lives on the
post page. Divar renders a "ارزیابی فروشنده" (seller assessment) block with
engine, chassis, body and gearbox states, plus colour, gearbox type, fuel and
insurance, and a relative publish time such as "۶ روز پیش".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from . import catalog as catalog_mod
from . import text as text_utils

# Condition vocabularies (Divar is not fully consistent, so both spellings appear).
# Normalised seller-evaluation values, keyed by Divar's exact wording.
# Paintwork is what matters for resale, so cosmetic scratches stay distinct from
# "repainted" rather than being lumped in with it.
CONDITION_GOOD = {
    "سالم": "سالم",
    "سالم و پلمپ": "سالم",
    "سالم و بی خط و خش": "بی‌خط و خش",
    "سالم و بی‌خط و خش": "بی‌خط و خش",
    "بی خط و خش": "بی‌خط و خش",
    "بی‌خط و خش": "بی‌خط و خش",
    "خط و خش جزیی": "خط و خش جزیی",
    "خط و خش جزئی": "خط و خش جزیی",
    "کم رنگ": "رنگ‌شده",
    "کم‌رنگ": "رنگ‌شده",
    "رنگ شده": "رنگ‌شده",
    "رنگ‌شده": "رنگ‌شده",
    "تعویض شده": "تعویض‌شده",
    "تعویض‌شده": "تعویض‌شده",
    "دارد": "آسیب‌دارد",
    "دارد (در توضیحات)": "آسیب‌دارد",
    "نیاز به تعمیر": "آسیب‌دارد",
    "نامشخص": "نامشخص",
    "دست دوم": "نامشخص",
}

GEARBOX = {
    "دنده‌ای": "دستی",
    "دستی": "دستی",
    "اتومات": "اتومات",
    "اتوماتیک": "اتومات",
    "نیمه‌اتومات": "نیمه‌اتومات",
    "CVT": "اتومات",
}

FUEL = {
    "بنزین": "بنزین",
    "دیزل": "دیزل",
    "گازوئیل": "دیزل",
    "دوگانه": "دوگانه",
    "هیبرید": "هیبرید",
    "برقی": "برقی",
}

BODY_WORDS = {
    "سدان": "سدان",
    "هاچبک": "هاچبک",
    "وانت": "وانت",
    "پیکاپ": "وانت",
    "کپ": "وانت",
    "استیشن": "استیشن",
    "وان": "وان",
    "کوپه": "کوپه",
    "کابیولت": "کوپه",
    "تیپ": "تیپ",
    # catalog paths use English slugs
    "sedan": "سدان",
    "hatchback": "هاچبک",
    "hatch": "هاچبک",
    "pickup": "وانت",
    "truck": "وانت",
    "van": "وان",
    "wagon": "استیشن",
    "estate": "استیشن",
    "coupe": "کوپه",
    "cabrio": "کوپه",
}

_REL_TIME = re.compile(
    r"(?P<n>\d+)\s*(?P<unit>دقیقه|ساعت|روز|هفته|ماه|سال)\s*(?P<ago>پیش|قبل)"
)
_ABS_TIME = re.compile(
    r"\b(?P<d>\d{1,2})\s+(?P<mon>فروردین|اردیبهشت|خرداد|تیر|مرداد|شهریور|مهر|آبان|"
    r"آذر|دی|بهمن|اسفند)\s+(?P<y>\d{3,4})\b"
)
_LABELS = (
    "موتور", "وضعیت شاسی‌ها", "وضعیت شاسی", "شاسی", "بدنه", "گیربکس", "رنگ",
    "مدل (سال تولید)", "مدل", "مهلت بیمه", "مهلت بیمهٔ شخص ثالث", "نوع سوخت",
    "قیمت پایه", "برند و مدل", "کارکرد",
)
_LABEL_ALIASES = {
    "وضعیت شاسی‌ها": "chassis",
    "وضعیت شاسی": "chassis",
    "شاسی": "chassis",
    "موتور": "engine",
    "بدنه": "body",
    "گیربکس": "gearbox",
    "رنگ": "paint",
    "نوع سوخت": "fuel",
    "مهلت بیمه": "insurance",
    "مهلت بیمهٔ شخص ثالث": "insurance",
    "برند و مدل": "trim",
}
_IGNORED_LABELS = {"مدل (سال تولید)", "مدل", "قیمت پایه", "کارکرد"}


@dataclass(slots=True)
class CarSpecs:
    paint: str | None = None
    chassis: str | None = None
    body: str | None = None
    engine: str | None = None
    gearbox_state: str | None = None
    gearbox: str | None = None
    fuel: str | None = None
    insurance: str | None = None
    trim: str | None = None
    body_type: str | None = None
    published_at: datetime | None = None
    published_text: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paint": self.paint,
            "chassis": self.chassis,
            "body": self.body,
            "engine": self.engine,
            "gearbox_state": self.gearbox_state,
            "gearbox": self.gearbox,
            "fuel": self.fuel,
            "insurance": self.insurance,
            "trim": self.trim,
            "body_type": self.body_type,
            "published_at": self.published_at.isoformat() if self.published_at else None,
            "published_text": self.published_text,
            "extra": self.extra,
        }

    @property
    def clean_body(self) -> bool:
        return self.paint == "بی‌خط و خش" and self.chassis == "سالم"


def normalize_condition(value: str | None) -> str | None:
    if not value:
        return None
    text = text_utils.normalize(value).replace("\u200c", " ")
    table = {catalog_mod.normalize(k): v for k, v in CONDITION_GOOD.items()}
    table.update({catalog_mod.normalize(k): v for k, v in GEARBOX.items()})
    table.update({catalog_mod.normalize(k): v for k, v in FUEL.items()})
    hit = table.get(text)
    if hit:
        return hit
    for key, value in table.items():
        if key and key in text:
            return value
    return None


_WORD_HOURS = {
    "لحظه": 1 / 60, "چند دقیقه": 10 / 60, "ربع": 15 / 60, "نیم ساعت": 0.5,
    "یک ربع": 0.25, "دقیقه": 1 / 60, "ساعت": 1.0, "روز": 24.0, "هفته": 168.0,
    "ماه": 720.0, "سال": 8760.0,
}
_NAMED_TIME_RE = re.compile(
    r"(?P<w1>لحظه|چند دقیقه|یک ربع|نیم ساعت|ربع)"
    r"(?:\s+(?P<w2>دقیقه|ساعت|روز|هفته|ماه|سال))?"
    r"\s*پیش"
)


def _parse_relative(text: str, now: datetime | None = None) -> datetime | None:
    now = now or datetime.now(timezone.utc)
    match = _REL_TIME.search(text)
    if match:
        n = int(match.group("n"))
        unit = match.group("unit")
        days = {"دقیقه": n / 1440, "ساعت": n / 24, "روز": n,
                "هفته": n * 7, "ماه": n * 30, "سال": n * 365}[unit]
        return now - timedelta(days=days)

    named = _NAMED_TIME_RE.search(text)
    if named:
        word = named.group("w1")
        unit = named.group("w2")
        if unit:
            if word in ("یک ربع", "ربع", "نیم ساعت"):
                base = {"دقیقه": 0.25, "ساعت": 0.5}.get(unit)
                if base is None:
                    base = 15 / 60
            else:
                base = _WORD_HOURS.get(word, 1 / 60)
        else:
            base = _WORD_HOURS.get(word, 1 / 60)
        return now - timedelta(hours=base)
    return None


_PERSIAN_MONTHS = {
    "فروردین": 1, "اردیبهشت": 2, "خرداد": 3, "تیر": 4, "مرداد": 5, "شهریور": 6,
    "مهر": 7, "آبان": 8, "آذر": 9, "دی": 10, "بهمن": 11, "اسفند": 12,
}


def _parse_absolute(text: str) -> datetime | None:
    match = _ABS_TIME.search(text_utils.normalize(text))
    if not match:
        return None
    day = int(match.group("d"))
    month = _PERSIAN_MONTHS.get(match.group("mon"))
    year = int(match.group("y"))
    if year < 1000:
        year += 1300
    if not month or year < 1360:
        return None
    gregorian = year + 621
    try:
        base = datetime(gregorian, month, day, tzinfo=timezone.utc)
    except ValueError:
        return None
    return base


def parse_publish_time(text: str) -> tuple[datetime | None, str | None]:
    """Return (published_at, human text) from a Divar timestamp line."""
    normalized = text_utils.normalize(text)
    for pattern in (_REL_TIME, _NAMED_TIME_RE):
        for match in pattern.finditer(normalized):
            label = " ".join(match.group(0).split())
            return _parse_relative(match.group(0)), label
    for match in _ABS_TIME.finditer(normalized):
        return _parse_absolute(match.group(0)), " ".join(match.group(0).split())
    return None, None


def _label_value_pairs(lines: list[str]) -> dict[str, list[str]]:
    """Match spec labels to their values, keeping every value per label.

    Divar renders the spec table RTL, which produces runs of labels followed by a
    run of values::

        کارکرد
        مدل (سال تولید)
        رنگ
        300000
        1394 - 2015
        سفید

    So a label's value is not always the next line. We queue labels and hand each
    following non-label value to the oldest label still waiting.

    A label can legitimately repeat: ``گیربکس`` appears once in the spec table
    (``دنده‌ای`` = the type) and again in the seller-evaluation block
    (``سالم و پلمپ`` = its condition). Both values are kept so they can be told apart.
    """
    label_keys = {catalog_mod.normalize(label): label for label in _LABELS}
    out: dict[str, list[str]] = {}
    pending: list[str] = []
    for line in lines:
        text = line.strip()
        if not text:
            continue
        label = label_keys.get(catalog_mod.normalize(text))
        if label is not None:
            if label not in pending:
                pending.append(label)
            continue
        if not pending:
            continue
        waiting = pending.pop(0)
        out.setdefault(waiting, []).append(text)
    return out


def _first(values: list[str], table: dict[str, str]) -> str | None:
    """First value that matches ``table``, then any value as a last resort."""
    normalized = {catalog_mod.normalize(k): v for k, v in table.items()}
    for value in values:
        hit = normalized.get(text_utils.normalize(value).replace("\u200c", " "))
        if hit:
            return hit
    for value in values:
        hit = normalized.get(text_utils.normalize(value).replace("\u200c", " "))
        if hit:
            return hit
    return None


def _condition(values: list[str]) -> str | None:
    """First value that looks like a condition verdict."""
    normalized = {catalog_mod.normalize(k): v for k, v in CONDITION_GOOD.items()}
    for value in values:
        key = catalog_mod.normalize(value)
        if key in normalized:
            return normalized[key]
    for value in values:
        key = catalog_mod.normalize(value)
        for table_key, table_value in normalized.items():
            if table_key and table_key in key:
                return table_value
    return None


def parse_specs(
    page_text: str, now: datetime | None = None, catalog: Any = None
) -> CarSpecs:
    """Extract car specs from the rendered text of a Divar post page."""
    lines = [l.strip() for l in page_text.splitlines() if l.strip()]
    if not lines:
        return CarSpecs()

    pairs = _label_value_pairs(lines)

    def values_for(label: str) -> list[str]:
        return pairs.get(label, [])

    def scalar(label: str) -> str | None:
        found = values_for(label)
        return found[0] if found else None

    specs = CarSpecs()
    published_at, published_text = parse_publish_time(page_text)
    specs.published_at = published_at
    specs.published_text = published_text

    paint_values = values_for("رنگ")
    specs.paint = _condition(paint_values) or _find_color(lines)
    specs.engine = _condition(values_for("موتور"))
    specs.chassis = _condition(values_for("وضعیت شاسی‌ها") + values_for("شاسی"))
    specs.body = _condition(values_for("بدنه"))
    # gearbox: the type from the spec table, the condition from the seller block
    specs.gearbox = _first(values_for("گیربکس"), GEARBOX)
    specs.gearbox_state = _condition(values_for("گیربکس"))
    specs.fuel = _first(values_for("نوع سوخت"), FUEL)
    specs.insurance = scalar("مهلت بیمهٔ شخص ثالث") or scalar("مهلت بیمه")
    specs.trim = scalar("برند و مدل")
    specs.body_type = _find_body(catalog)

    for label, found in pairs.items():
        if label not in _LABELS and found:
            specs.extra[label] = found[0]

    return specs


_COLORS = (
    "سفید", "مشکی", "نقره‌ای", "نقره ای", "خاکستری", "طوسی", "آبی", "قرمز", "سبز",
    "زرد", "نارنجی", "قهوه‌ای", "بژ", "کرم", "طلایی", "بنفش", "صورتی", "سفید صدفی",
)
_SELLER_BLOCK = re.compile(r"ارزیابی فروشنده")


def _find_color(lines: list[str]) -> str | None:
    for line in lines:
        if line in _COLORS:
            return line
    for i, line in enumerate(lines):
        if line == "رنگ" and i + 1 < len(lines):
            value = lines[i + 1].strip()
            if value in _COLORS:
                return value
    return None


def _find_body(catalog: Any = None) -> str | None:
    """Body style cannot be read from a post page.

    It used to be guessed by scanning the whole catalog, which returned the first
    model name that happened to contain a body word ("وانت") for *every* car.
    Body style is now derived from the resolved catalog path via
    :func:`body_for_path`, which is the only source that describes this listing.
    """
    return None


def body_for_path(vehicle_path: str | None) -> str | None:
    if not vehicle_path:
        return None
    tail = vehicle_path.split("/")[-1].lower()
    for word, label in BODY_WORDS.items():
        if word in tail:
            return label
    return None


def specs_from_dom(page: Any) -> CarSpecs:
    """Preferred path: read the spec table straight from the rendered DOM."""
    try:
        text = page.evaluate("() => document.body.innerText")
    except Exception:  # noqa: BLE001
        return CarSpecs()
    return parse_specs(text or "")
