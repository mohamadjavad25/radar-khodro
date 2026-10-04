"""Persian text normalization and number/price parsing utilities."""

from __future__ import annotations

import re
import unicodedata

PERSIAN_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
ARABIC_DIGITS = "٠١٢٣٤٥٦٧٨٩"

_TRANSLATE: dict[int, str] = {}
for _index, _char in enumerate(PERSIAN_DIGITS + ARABIC_DIGITS):
    _TRANSLATE[ord(_char)] = str(_index % 10)

_TRANSLATE.update(
    {
        ord("ي"): "ی",
        ord("ى"): "ی",
        ord("ك"): "ک",
        ord("ڪ"): "ک",
        ord("ة"): "ه",
        ord("أ"): "ا",
        ord("إ"): "ا",
        ord("آ"): "ا",
        ord("ٱ"): "ا",
        ord("ـ"): "",
        ord("‌"): " ",
        ord("‍"): " ",
        ord("‎"): " ",
        ord("‏"): " ",
        ord("٫"): ".",
        ord("٬"): ",",
        ord("،"): "،",
    }
)

_NEGOTIABLE_MARKERS = (
    "قابل توافق",
    "قابل مذاکره",
    "قابل گفتگو",
    "توافقی",
    "توافق بشه",
    "تماس بگیرید",
    "تماس بگیر",
    "تماس بگیریم",
    "هر قیمت",
    "قیمت مناسب",
    "قیمت پایین",
    "بدون قیمت",
    "مذاکره",
    "call",
)

_SCALE_WORDS: tuple[tuple[str, float], ...] = (
    ("میلیارد", 1_000_000_000.0),
    ("میلیون", 1_000_000.0),
    ("هزار", 1_000.0),
)

_CURRENCY_FACTORS: tuple[tuple[str, float], ...] = (
    ("ریال", 0.1),
    ("تومان", 1.0),
    ("تومن", 1.0),
)

_NUMBER_RE = re.compile(r"\d[\d,.\u066c]*")
_WHITESPACE_RE = re.compile(r"\s+")


def to_latin_digits(text: str) -> str:
    return text.translate(_TRANSLATE)


def normalize(text: str | None) -> str:
    """Normalize Persian text for matching: unified digits, letters, spacing."""
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return _WHITESPACE_RE.sub(" ", value).strip()


def normalize_digits(text: str | None) -> str:
    return to_latin_digits(normalize(text))


def is_negotiable(text: str | None) -> bool:
    lowered = normalize(text).lower()
    return any(marker in lowered for marker in _NEGOTIABLE_MARKERS)


def parse_number(token: str) -> float | None:
    """Parse a numeric token that may use Persian or Western grouping separators."""
    if not token:
        return None
    cleaned = to_latin_digits(token).strip().replace(",", "").replace("\u066c", "")
    if not cleaned:
        return None

    if "." in cleaned:
        head, _, tail = cleaned.partition(".")
        if head.isdigit() and tail.isdigit():
            return float(f"{head}.{tail}")
        cleaned = cleaned.replace(".", "")

    if cleaned.isdigit():
        return float(cleaned)
    return None


def iter_numbers(text: str) -> list[tuple[str, float]]:
    """Return (token, value) pairs for every numeric token in text."""
    haystack = to_latin_digits(text)
    results: list[tuple[str, float]] = []
    for match in _NUMBER_RE.finditer(haystack):
        value = parse_number(match.group(0))
        if value is not None:
            results.append((match.group(0), value))
    return results


def _scale_after(text: str, position: int) -> float:
    tail = text[position : position + 24].lower()
    for word, factor in _SCALE_WORDS:
        if re.match(rf"\s*{re.escape(word)}", tail):
            return factor
    return 1.0


def _currency_factor(text: str, position: int) -> float:
    tail = text[position : position + 32].lower()
    for word, factor in _CURRENCY_FACTORS:
        if word in tail:
            return factor
    return 1.0


MIN_CAR_PRICE_TOMAN = 10_000_000


def parse_price(text: str | None, min_toman: int = MIN_CAR_PRICE_TOMAN) -> tuple[int | None, bool]:
    """Return (price_in_toman, is_negotiable) for a free-form price expression."""
    raw = normalize(text)
    if not raw:
        return None, False

    negotiable = is_negotiable(raw)
    lowered = raw.lower()
    haystack = to_latin_digits(lowered)

    best: int | None = None
    for match in _NUMBER_RE.finditer(haystack):
        value = parse_number(match.group(0))
        if value is None or value <= 0:
            continue

        end = match.end()
        amount = value * _scale_after(lowered, end) * _currency_factor(lowered, end)
        toman = int(round(amount))

        if toman < min_toman:
            continue
        if best is None or toman > best:
            best = toman

    if best is None:
        return None, negotiable
    return best, negotiable


def parse_price_digits(text: str | None) -> int | None:
    """Parse a raw price field that may already be a plain integer amount."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        value = float(text)
    else:
        raw = str(text).strip()
        if not raw:
            return None
        value = parse_number(raw)
        if value is None:
            return None
    if value <= 0:
        return None
    return int(value)


def format_toman(amount: int | None) -> str:
    if amount is None:
        return "—"
    if amount >= 1_000_000_000:
        return f"{amount / 1_000_000_000:.1f} میلیارد"
    if amount >= 1_000_000:
        return f"{amount // 1_000_000} میلیون"
    if amount >= 1_000:
        return f"{amount // 1_000} هزار"
    return str(amount)


def format_toman_full(amount: int | None) -> str:
    if amount is None:
        return "—"
    return f"{amount:,} تومان"
