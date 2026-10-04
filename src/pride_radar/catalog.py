"""Vehicle catalog: brands, models and trims, plus Persian name matching.

Built from Divar's own ``filters-config.json`` so it stays in sync with the
marketplace instead of hardcoding a single brand.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CATEGORY_LIGHT = "light"
CATEGORY_HEAVY = "heavy"
CATEGORY_MOTOR = "motorcycle"
CATEGORY_MACHINE = "machine"

CATEGORIES: dict[str, str] = {
    CATEGORY_LIGHT: "سواری و وانت",
    CATEGORY_HEAVY: "سنگین",
    CATEGORY_MOTOR: "موتورسیکلت",
    CATEGORY_MACHINE: "موتور و ماشین‌آلات",
}

CATEGORY_SLUGS: dict[str, str] = {
    CATEGORY_LIGHT: "car",
    CATEGORY_HEAVY: "heavy-car",
    CATEGORY_MOTOR: "motorcycle",
    CATEGORY_MACHINE: "machine",
}

# Persian display names keyed by Divar's brand path.
BRAND_FA: dict[str, str] = {
    "pride": "پراید", "peugeot": "پژو", "citroen": "سیتروئن", "renault": "رنو",
    "ford": "فورد", "opel": "اوپل", "mercedes-benz": "مرسدس بنز", "bmw": "بی‌ام‌و",
    "audi": "آئودی", "volkswagen": "فولکس", "volvo": "ولوو", "toyota": "تویوتا",
    "nissan": "نیسان", "honda": "هیوندا", "hyundai": "هیوندای", "kia": "کیا",
    "mazda": "مزدا", "mitsubishi": "میتسوبیشی", "subaru": "سابارو", "suzuki": "سوزوکی",
    "dacia": "داچیا", "fiat": "فیات", "seat": "-seat", "skoda": "اسکودا",
    "chery": "چری", "geely": "جیلی", "haval": "هاوال", "jac": "جی‌ای‌سی",
    "mg": "ام‌جی", "byd": "بی‌وی‌دی", "dongfeng": "دنگ‌فنگ", "baic": "بایک",
    "iran": "ایران خودرو", "saipa": "سایپا", "bahman": "بهمن", "sahand": "سهاند",
    "shahin": "شاهین", "samand": "سمند", "dena": "دنا", "tiba": "تیبا",
    "quick": "کوییک", "sorous": "سورن", "ascent": "آسانس", "sedan": "سمند سورن",
    "tractor": "TRACTOR", "siege": "سیج", "heavy": "سنگین", "light": "سواری",
    "rex": "رکس", "tractor-registrator": "رجیستریتور",
    "mahindra": "ماهیندا", "pajero": "پاجرو", "pajero-sport": "پاجرو اسپرت",
    "lifan": "لیفان", "lifanc2": "لیفان C2", "lifanx50": "لیفان X50",
    "brilliance": "بریلیانس", "zotye": "زوتی", "haima": "هایما", "enix": "ای‌نیکس",
    "newgenix": "نیو‌جنیکس", "bim electric": "بیم الکتریک",
    "royalstar": "رویال‌استار", "mh115es": "ام‌اچ۱۱۵", "mpt-155": "ام‌پی‌تی",
    "sako": "ساکو", "sahand-sx": "سهاند SX",
    "tender": "تندر", "megane": "مگان", "clio": "کلیو", "captur": "کاپتور",
    "titanium": "تیتانیوم", "primero": "پریمرو", "patrol": "پاترول",
    "elan": "النترا", "santafe": "سانتافه", "tucson": "توسان",
    "optima": "اپتیما", "rio": "ریو", "picanto": "پیکانتو",
    "camry": "کمری", "corolla": "کورولا", "landcruiser": "لندکروزر",
    "octavia": "اکتاویا", "ibiza": "ایبیزا", "leon": "لئون",
    "mustang": "ماستنگ", "focus": "فوکوس", "fiesta": "فیستا", "figo": "فیگو",
    "ranger": "رنجر", "ecosport": "اکواسپورت", "everest": "اورستر", "edge": "اج",
    "fusion": "فیوژن", "sail": "سیل", "tahoe": "تاهو",
    "emgrand": "امگرند", "coolray": "کول‌ری", "chery": "چری",
    "seat": "سیت", "altonaar": "آلتونا",
}

# Persian display names for model paths (brand/model or brand/model/trim).
MODEL_FA: dict[str, str] = {
    # Pride
    "pride/111": "پراید ۱۱۱", "pride/131": "پراید ۱۳۱", "pride/132": "پراید ۱۳۲",
    "pride/141": "پراید ۱۴۱", "pride/pickup": "وانت پراید", "pride/saba-glxi": "پراید صبا",
    "pride/hatchback": "پراید هاچبک", "pride/sedan": "پراید سدان",
    # Peugeot
    "peugeot/206": "پژو ۲۰۶", "peugeot/207i": "پژو ۲۰۷", "peugeot/207": "پژو ۲۰۷",
    "peugeot/405": "پژو ۴۰۵",
    "peugeot/406": "پژو ۴۰۶", "peugeot/407": "پژو ۴۰۷", "peugeot/pars": "پژو پارس",
    "peugeot/301": "پژو ۳۰۱", "peugeot/308": "پژو ۳۰۸", "peugeot/2008": "پژو ۲۰۰۸",
    "peugeot/208": "پژو ۲۰۸", "peugeot/107": "پژو ۱۰۷",
    # Iran Khodro / Saipa
    "sahand": "سهاند", "sahand/sx5": "سهاند SX5", "sahand/sx6": "سهاند SX6",
    "sahand/ex": "سهاند EX", "shahin": "شاهین", "shahin/restyle": "شاهین",
    "dena": "دنا", "dena/plus": "دنا پلاس", "dena/lx": "دنا LX",
    "dena/turbo": "دنا توربو", "dena/turbo-plus": "دنا توربو پلاس",
    "samand": "سمند", "samand/lx": "سمند LX", "samand/sx": "سمند SX",
    "samand/ex": "سمند EX", "samand/e-lx": "سمند E LX", "samand/e-x7": "سمند E X7",
    "tiba": "تیبا", "tiba/1": "تیبا ۱", "tiba/2": "تیبا ۲",
    "quick": "کوییک", "quick/r": "کوییک R", "quick/s": "کوییک S",
    "sorous": "سورن", "sorous/elx": "سورن ال‌ایکس", "sorous/sx7": "سورن توربو",
    "ascent": "آسانس", "tractor-registrator": "تراکتور رجیستریتور",
    "sahand-electric": "سهاند برقی", "rex": "رکس",
    # Popular imports (paths verified against Divar's live filter config)
    "renault/tondar-90": "رنو تندر ۹۰", "renault/tondar-90-plus": "رنو تندر ۹۰ پلاس",
    "renault/pars-tondar": "رنو پارس تندر", "renault/duster": "رنو داستر",
    "renault/sandero": "رنو ساندرو", "renault/megan": "رنو مگان",
    "renault/clio": "رنو کلیو", "renault/captur": "رنو کاپتور",
    "duster": "داستر", "dacia/duster": "داستر", "dacia/sandero": "ساندرو",
    "hyundai/elantra-ir": "هیوندای النترا", "hyundai/elantra": "هیوندای النترا",
    "hyundai/i30": "هیوندای i30", "hyundai/accent": "هیوندای آکسنت",
    "hyundai/santafe": "سانتافه", "hyundai/tucson": "توسان",
    "kia/optima": "کیا اپتیما", "kia/rio": "کیا ریو",
    "nissan/tit-an": "نیسان تیتانیوم", "nissan/primero": "نیسان پریمرو",
    "fiat/500": "فیات ۵۰۰", "fiat/ducato": "فیات دوکاتو", "fiat/panda": "فیات پاندا",
    "chevrolet/tahoe": "شورولت تاهو", "chevrolet/sail": "شورولت سیل",
    "ford/figo": "فیگو", "ford/focus": "فورد فوکوس", "ford/mustang": "فورد ماستنگ",
    "ford/ranger": "فورد رنجر", "ford/ecosport": "فورد اکواسپورت",
    "ford/everest": "فورد اورستر", "ford/edge": "فورد اج",
    "ford/fiesta": "فورد فیستا", "ford/fusion": "فورد فیوژن",
    "mazda/3": "مزدا ۳", "mazda/6": "مزدا ۶", "mazda/323": "مزدا ۳۲۳",
    "toyota/camry": "تویوتا کمری", "toyota/corolla": "کورولا",
    "toyota/land-cruiser": "لندکروزر", "skoda/octavia": "اسکودا اکتاویا",
    "mg/5": "ام‌جی ۵", "chery/e5": "چری E5", "chery/e8": "چری E8",
    "haval/h6": "هاوال H6", "haval/j6": "هاوال J6",
    "geely/emgrand": "جیلی امگرند", "geely/coolray": "جیلی کول‌ری",
    "lifanc2": "لیفان C2", "lifanx50": "لیفان X50",
}

# Persian aliases that map onto a canonical path (search input -> path).
ALIASES: dict[str, str] = {
    "پژو۲۰۶": "peugeot/206", "۲۰۶": "peugeot/206", "206": "peugeot/206",
    "پژو۲۰۷": "peugeot/207i", "۲۰۷": "peugeot/207i", "207": "peugeot/207i",
    "پژو۴۰۵": "peugeot/405", "۴۰۵": "peugeot/405",
    "پژو۴۰۶": "peugeot/406", "۴۰۶": "peugeot/406",
    "پژوپارس": "peugeot/pars", "پژوپارسlx": "peugeot/pars",
    "پراید۱۱۱": "pride/111", "پراید۱۳۱": "pride/131", "پراید۱۳۲": "pride/132",
    "پراید۱۴۱": "pride/141", "وانت۱۵۱": "pride/pickup", "وانتپراید": "pride/pickup",
    "صبا": "pride/saba-glxi", "پرایدصبا": "pride/saba-glxi",
    "تیبا۱": "tiba/1", "تیبا۲": "tiba/2", "تیبا": "tiba",
    "دنالوکس": "dena/lx", "دنالکس": "dena/lx", "دناپلاس": "dena/plus",
    "دناپلاستوربو": "dena/turbo-plus", "دناتوربو": "dena/turbo",
    "سمندالکس": "samand/lx", "سمنداسکس": "samand/sx", "سمننداکس": "samand/ex",
    "کوییکآر": "quick/r", "کوییکاس": "quick/s",
    "سهانداسکس۶": "sahand/sx6", "سهانداسکس۵": "sahand/sx5",
    "رنوتندر": "renault/tender", "داستر": "duster",
    "نیسانتیتانیوم": "nissan/titanium", "نیسانتیتانیوم۹۰": "nissan/tit-an",
    "هیوندایالنترا": "hyundai/elantra-ir",
    "رنوتندر": "renault/tondar-90", "رنوتندر۹۰": "renault/tondar-90",
    "دناپلاس": "dena/plus", "دنالوکس": "dena/lx", "دنالکس": "dena/lx",
    "دناتوربو": "dena/turbo", "دناپلاستوربو": "dena/turbo-plus",
    "فیات۵۰۰": "fiat/500", "فوردفیستا": "ford/fiesta", "فوردفیگو": "ford/figo",
    "تویوتاکمری": "toyota/camry", "کیااپتیما": "kia/optima",
    "شورولتسیل": "chevrolet/sail", "شاهین": "shahin/g", "سهاند": "sahand",
    "سهاندپلاس": "sahand/plus", "سورن": "sorous", "سورنتوربو": "sorous/turbo",
    "آسانس": "ascent", "تراکتور": "tractor-registrator",
    "مزدا۳": "mazda/3", "اسکودااکتاویا": "skoda/octavia",
    "ساندرو": "dacia/sandero", "تندر": "renault/tondar-90", "تیتانیوم": "nissan/titanium",
    "النترا": "hyundai/elantra-ir", "آکسنت": "hyundai/accent",
    "کمری": "toyota/camry", "اپتیما": "kia/optima", "پاترول": "nissan/patrol",
    "تاهو": "chevrolet/tahoe", "فیستا": "ford/fiesta", "فیگو": "ford/figo",
    "کاپتور": "renault/captur", "کلیو": "renault/clio", "کورولا": "toyota/corolla",
    # Common misspellings and colloquial forms
    "پرابد": "pride", "پرايد": "pride", "پراید۱۳۱": "pride/131",
    "پژو۲۰۶": "peugeot/206", "پژو206": "peugeot/206", "پژو۲۰۷": "peugeot/207i",
    "سمندال": "samand", "دناسد": "dena", "دناتوربو": "dena/turbo",
    "تیبا۱": "tiba/1", "تیبا۲": "tiba/2", "وانتپراید۱۵۱": "pride/pickup",
    "پرایدوانت": "pride/pickup", "کوییک": "quick", "سورن": "sorous",
    "رنو": "renault", "نیسان": "nissan", "هیوندا": "hyundai", "مزدا": "mazda",
}

# Words that must never be read as a model name.
STOPWORDS = {
    "فروش", "قیمت", "مدل", "سال", "کارکرد", "تومان", "میلیون", "میلیارد",
    "ریال", "شاسی", "سالم", "تمیز", "کارکرد", "دوگانه", "بی", "رنگ", "مدل",
    "اتومات", "دستی", "گیربکس", "مشاوره", "خریدار", "فروشی", "معامله",
    "معاوضه", "فوری", "ویژه", "شرایطی", "نقدی", "اقساطی", "صفر", "کیلومتر",
    "دست‌اول", "کارکرده", "نیازمند", "تعویض", "گیربکس", "داخلی", "خارجی",
    "شهر", "اهواز", "خودرو", "ماشین", "آگهی", "تماس", "ببینید",
}

_AR_DIGITS = "٠١٢٣٤٥٦٧٨٩"
_FA_DIGITS = "۰۱۲۳۴۵۶۷۸۹"
_TRANSLATE: dict[int, str] = {}
for _i, _c in enumerate(_FA_DIGITS + _AR_DIGITS):
    _TRANSLATE[ord(_c)] = str(_i % 10)
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
        ord("ـ"): "",
        ord("‌"): " ",
        ord("‍"): "",
        ord("‎"): " ",
        ord("‏"): " ",
        ord("٫"): ".",
        ord("٬"): ",",
    }
)
_WS = re.compile(r"\s+")
_NON_KEY = re.compile(r"[^؀-ۿa-z0-9]+")


def normalize(text: str | None) -> str:
    if not text:
        return ""
    value = unicodedata.normalize("NFKC", text).translate(_TRANSLATE)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return _WS.sub(" ", value).strip().lower()


def key(text: str | None) -> str:
    """Fold a string into a comparable key (digits unified, separators dropped)."""
    return _NON_KEY.sub("", normalize(text))


@dataclass(slots=True, frozen=True)
class Model:
    name: str          # Divar display name, e.g. "Pride 131"
    path: str          # "pride/131"
    brand: str         # "Pride"
    brand_path: str    # "pride"
    depth: int         # 1 = brand, 2 = model, 3+ = trim
    fa: str = ""       # Persian label when known
    variants: tuple[str, ...] = ()

    @property
    def is_brand(self) -> bool:
        return self.depth == 1

    @property
    def label_fa(self) -> str:
        return self.fa or self.name

    @property
    def path_segments(self) -> tuple[str, ...]:
        return tuple(self.path.split("/"))

    def url_path(self) -> str:
        return self.path


@dataclass(slots=True)
class Catalog:
    models: list[Model] = field(default_factory=list)
    brands: list[Model] = field(default_factory=list)
    by_path: dict[str, Model] = field(default_factory=dict)
    by_name: dict[str, Model] = field(default_factory=dict)
    by_key: dict[str, Model] = field(default_factory=dict)
    _keys_sorted: list[tuple[str, Model]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.models)

    def index(self) -> None:
        self.by_path.clear()
        self.by_name.clear()
        self.by_key.clear()
        self._keys_sorted = []
        for m in self.models:
            self.by_path.setdefault(m.path, m)
            self.by_name.setdefault(m.name.lower(), m)
            labels = [m.name, m.fa, *m.variants]
            # Compose a Persian label for brand+model entries that lack one.
            if not m.fa and m.depth == 2:
                brand_fa = BRAND_FA.get(m.brand_path, "")
                model_slug = m.path_segments[-1]
                if brand_fa and model_slug:
                    labels.append(f"{brand_fa} {model_slug}")
            for label in labels:
                if label:
                    self.by_key.setdefault(key(label), m)
            self.by_key.setdefault(key(m.path.replace("/", " ")), m)
        # Register aliases.
        for alias, path in ALIASES.items():
            m = self.by_path.get(path)
            if m is not None:
                self.by_key.setdefault(key(alias), m)
        self._keys_sorted = sorted(self.by_key.items(), key=lambda kv: len(kv[0]), reverse=True)

    def brands_for(self, category: str = CATEGORY_LIGHT) -> list[Model]:
        return [b for b in self.brands if b.brand_path and b.depth == 1 and _cat(b) == category]

    def children_of(self, brand_path: str) -> list[Model]:
        prefix = brand_path + "/"
        out = [m for m in self.models if m.path.startswith(prefix) and m.depth == 2]
        return sorted(out, key=lambda m: m.name)

    def find(self, text: str) -> Model | None:
        k = key(text)
        if not k:
            return None
        return self.by_key.get(k)

    def match_in(self, text: str) -> Model | None:
        """Identify the most specific vehicle mentioned in a title.

        Prefers depth-2 (model) matches over brands and trims so that
        "پژو ۲۰۷" does not resolve to a 206 trim by accident.
        """
        hay = key(text)
        if not hay:
            return None
        best: Model | None = None
        best_len = 0
        for k, model in self._keys_sorted:
            if len(k) < 3 or k not in hay:
                continue
            if best is None:
                best, best_len = model, len(k)
                continue
            if model.depth == 1 and best.depth != 1:
                continue
            if model.depth >= 3 and best.depth == 2:
                continue
            if len(k) > best_len:
                best, best_len = model, len(k)
        return best

    def resolve_path(self, path: str) -> Model | None:
        return self.by_path.get(path.strip("/").lower())

    def to_json(self) -> str:
        return json.dumps(
            {
                "brands": [
                    {"path": b.path, "name": b.name, "fa": b.fa, "category": _cat(b)}
                    for b in self.brands
                ],
                "models": [
                    {
                        "path": m.path,
                        "name": m.name,
                        "fa": m.fa,
                        "brand": m.brand,
                        "depth": m.depth,
                    }
                    for m in self.models
                ],
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: dict) -> Catalog:
        cat = cls()
        for b in data.get("brands", []):
            cat.brands.append(
                Model(
                    name=b["name"],
                    path=b["path"],
                    brand=b["name"],
                    brand_path=b["path"],
                    depth=1,
                    fa=b.get("fa", ""),
                )
            )
        for m in data.get("models", []):
            cat.models.append(
                Model(
                    name=m["name"],
                    path=m["path"],
                    brand=m.get("brand", ""),
                    brand_path=m["path"].split("/")[0],
                    depth=int(m.get("depth", 2)),
                    fa=m.get("fa", ""),
                )
            )
        cat.index()
        return cat

    def save(self, path: str | Path) -> None:
        Path(path).write_text(self.to_json(), encoding="utf-8")


def _cat(model: Model) -> str:
    return model.path.split("/")[0] if "/" in model.path else "misc"


def _fa_for(path: str, name: str) -> str:
    if path in MODEL_FA:
        return MODEL_FA[path]
    brand = path.split("/")[0]
    if path in BRAND_FA:
        return BRAND_FA[path]
    if brand in BRAND_FA and path.count("/") == 0:
        return BRAND_FA[brand]
    if "/" not in path and name:
        return name
    return ""


def build_from_filters(payload: dict, category: str = CATEGORY_LIGHT) -> Catalog:
    """Build a catalog from Divar's filters-config.json payload."""
    values = payload.get("path_filters_values_per_category", {}) or {}
    node = values.get(category) or {}
    raw = node.get("brand_model") or {}

    cat = Catalog()
    seen_paths: set[str] = set()

    for name, path in raw.items():
        clean = str(path).strip("/").lower()
        if not clean or clean in seen_paths:
            continue
        seen_paths.add(clean)
        segments = clean.split("/")
        brand = segments[0]
        depth = len(segments)
        cat.models.append(
            Model(
                name=str(name),
                path=clean,
                brand=str(name).split()[0] if depth == 1 else name.split()[0],
                brand_path=brand,
                depth=depth,
                fa=_fa_for(clean, str(name)),
            )
        )
        if depth == 1:
            cat.brands.append(
                Model(
                    name=str(name),
                    path=clean,
                    brand=str(name),
                    brand_path=clean,
                    depth=1,
                    fa=_fa_for(clean, str(name)),
                )
            )

    cat.brands.sort(key=lambda m: m.name)
    cat.models.sort(key=lambda m: m.path)
    _add_missing_brands(cat, payload, category)
    cat.index()
    return cat


# Brands Divar groups oddly or nests under another name but that users search for.
EXTRA_BRANDS: dict[str, tuple[str, str]] = {
    "sahand": ("سهاند", "sahand"),
    "sorous": ("سورن", "sorous"),
    "ascent": ("آسانس", "ascent"),
    "shahin": ("شاهین", "shahin"),
    "tractor-registrator": ("تراکتور", "tractor-registrator"),
}

# Models Divar no longer lists but that remain common in the used market.
EXTRA_MODELS: dict[str, str] = {
    "nissan/titanium": "نیسان تیتانیوم",
    "nissan/primero": "نیسان پریمرو",
    "peugeot/207": "پژو ۲۰۷",
    "peugeot/405-convertible": "پژو ۴۰۵ کابRIOولت",
    "sahand/plus": "سهاند پلاس",
    "sahand/sx5": "سهاند SX5",
    "sahand/sx6": "سهاند SX6",
    "sorous/turbo": "سورن توربو",
    "sorous/elx": "سورن ال‌ایکس",
    "quick/r": "کوییک R",
    "quick/s": "کوییک S",
    "tiba/1": "تیبا ۱",
    "tiba/2": "تیبا ۲",
}


def _add_missing_brands(cat: Catalog, payload: dict, category: str) -> None:
    present = {m.brand_path for m in cat.models}
    for path, (fa, model_root) in EXTRA_BRANDS.items():
        if model_root in {m.path for m in cat.models} and path in present:
            continue
        if path not in present:
            cat.brands.append(
                Model(name=fa, path=model_root, brand=fa, brand_path=model_root, depth=1, fa=fa)
            )
        cat.models.append(
            Model(
                name=fa,
                path=model_root,
                brand=fa,
                brand_path=model_root,
                depth=1,
                fa=fa,
            )
        )

    known_paths = {m.path for m in cat.models}
    for path, fa in EXTRA_MODELS.items():
        if path in known_paths:
            continue
        brand_path = path.split("/")[0]
        if brand_path not in present and brand_path not in EXTRA_BRANDS:
            continue
        cat.models.append(
            Model(
                name=fa,
                path=path,
                brand=fa.split()[0],
                brand_path=brand_path,
                depth=len(path.split("/")),
                fa=fa,
            )
        )


def load_catalog(path: str | Path) -> Catalog:
    file_path = Path(path)
    if not file_path.is_file():
        return Catalog()
    return Catalog.from_dict(json.loads(file_path.read_text(encoding="utf-8")))


@lru_cache(maxsize=4)
def load_from_filters_file(path: str, category: str = CATEGORY_LIGHT) -> Catalog:
    file_path = Path(path)
    if not file_path.is_file():
        return Catalog()
    return build_from_filters(json.loads(file_path.read_text(encoding="utf-8")), category)
