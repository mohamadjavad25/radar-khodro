"""Configuration: city, watched vehicles, filters and alert rules."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

DEFAULT_CONFIG_NAME = "config.json"


@dataclass(slots=True)
class SearchConfig:
    city: str = "ahvaz"
    city_id: str = "7"
    category: str = "car"
    category_id: str = "light"
    pages: int = 3
    sort: str = "sort_date"
    query: str = ""
    fetch_all_cars: bool = False

    def web_url(self, brand_model: list[str] | None = None) -> str:
        from .client import page_url

        return page_url(self.city, self.category, self.query, brand_model)


@dataclass(slots=True)
class WatchConfig:
    interval_minutes: int = 10
    max_pages_per_run: int = 2
    delay_min_seconds: float = 5.0
    delay_max_seconds: float = 12.0
    max_runs: int = 0
    max_specs_per_run: int = 12


@dataclass(slots=True)
class VehicleTarget:
    """One thing to watch: a model, a brand, or everything in a category."""

    id: str
    label: str = ""
    paths: list[str] = field(default_factory=list)   # canonical catalog paths
    brand_paths: list[str] = field(default_factory=list)
    query: str = ""                                    # free-text fallback
    enabled: bool = True

    @property
    def is_all(self) -> bool:
        return not self.paths and not self.brand_paths and not self.query

    def catalog_terms(self) -> list[str]:
        """Display names Divar expects for the brand_model filter."""
        return list(self.paths)


@dataclass(slots=True)
class FilterConfig:
    min_year: int | None = None
    max_year: int | None = None
    max_mileage_km: int | None = None
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    exclude_business: bool = True
    exclude_suspicious: bool = True

    @property
    def all_models(self) -> list[str]:
        return []


@dataclass(slots=True)
class Rule:
    id: str
    name: str
    enabled: bool = True
    target_ids: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    min_year: int | None = None
    max_year: int | None = None
    max_mileage_km: int | None = None
    min_price_toman: int | None = None
    max_price_toman: int | None = None
    exclude_business: bool = False
    exclude_suspicious: bool = True
    max_discount_vs_market: float | None = None
    require_price: bool = True
    notify: bool = True


@dataclass(slots=True)
class DealConfig:
    """Hard requirements for a car to be worth the user's attention."""

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

    def to_settings(self) -> Any:
        from .deals import DealSettings

        return DealSettings(
            min_profit_toman=self.min_profit_toman,
            min_roi=self.min_roi,
            max_age_hours=self.max_age_hours,
            require_clean_body=self.require_clean_body,
            require_sound_chassis=self.require_sound_chassis,
            require_known_condition=self.require_known_condition,
            exclude_business=self.exclude_business,
            max_mileage_km=self.max_mileage_km,
            min_year=self.min_year,
            max_year=self.max_year,
            max_price_toman=self.max_price_toman,
            exclude_salvage=self.exclude_salvage,
        )


@dataclass(slots=True)
class ScoringConfig:
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "price": 0.40,
            "year": 0.25,
            "mileage": 0.20,
            "model": 0.15,
        }
    )
    market_max_age_hours: int = 96
    reference_year: int = 1404
    reference_mileage_km: int = 60_000


@dataclass(slots=True)
class NotifyConfig:
    console: bool = True
    desktop: bool = True
    sound: bool = False
    color: bool = True


@dataclass(slots=True)
class StorageConfig:
    db_path: str = "data/pride.db"
    filters_path: str = "data/divar-filters.json"
    catalog_path: str = "data/catalog-light.json"
    keep_days: int = 180


@dataclass(slots=True)
class Config:
    search: SearchConfig = field(default_factory=SearchConfig)
    watch: WatchConfig = field(default_factory=WatchConfig)
    targets: list[VehicleTarget] = field(default_factory=list)
    filters: FilterConfig = field(default_factory=FilterConfig)
    deals: DealConfig = field(default_factory=DealConfig)
    rules: list[Rule] = field(default_factory=list)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    notify: NotifyConfig = field(default_factory=NotifyConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    _config_dir: Path = field(default_factory=Path.cwd)

    @property
    def root(self) -> Path:
        """Project root: the parent of the directory holding the config file."""
        parent = self._config_dir.parent if self._config_dir.name == "data" else self._config_dir
        return parent if (parent / "src").is_dir() else self._config_dir

    def resolve(self, relative: str) -> Path:
        path = Path(relative)
        return path if path.is_absolute() else (self.root / path)

    @property
    def db_path(self) -> Path:
        return self.resolve(self.storage.db_path)

    @property
    def filters_path(self) -> Path:
        return self.resolve(self.storage.filters_path)

    @property
    def catalog_path(self) -> Path:
        return self.resolve(self.storage.catalog_path)

    @property
    def state_path(self) -> Path:
        return self.db_path.with_name("session.json")

    def target(self, target_id: str) -> VehicleTarget | None:
        return next((t for t in self.targets if t.id == target_id), None)

    @property
    def active_targets(self) -> list[VehicleTarget]:
        return [t for t in self.targets if t.enabled]

    def fetch_targets(self) -> list[VehicleTarget]:
        """Vehicles to actually query.

        Only enabled targets are scanned, so the scanner never sweeps every car
        in town by accident. Opt into a full category scan with
        ``search.fetch_all_cars``.
        """
        if self.search.fetch_all_cars:
            return [VehicleTarget(id="all-cars", label="همهٔ خودروها")]
        return self.active_targets


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _known(cls: type, data: dict[str, Any]) -> dict[str, Any]:
    allowed = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in allowed}


def _build_target(raw: Any, index: int) -> VehicleTarget:
    if isinstance(raw, str):
        return VehicleTarget(id=f"t{index + 1}", label=raw, query=raw)
    if not isinstance(raw, dict):
        raise ValueError(f"targets[{index}] باید رشته یا شیء باشد")

    paths = [str(p).strip("/").lower() for p in raw.get("paths", []) if p]
    brands = [str(p).strip("/").lower() for p in raw.get("brand_paths", []) if p]
    for path in paths:
        if "/" not in path and not brands:
            brands.append(path)

    query = str(raw.get("query", "") or "")
    label = str(raw.get("label", "") or "")
    if not label:
        label = query or (paths[0] if paths else (brands[0] if brands else "همهٔ خودروها"))
    if not (paths or brands or query):
        label = "همهٔ خودروها"

    return VehicleTarget(
        id=str(raw.get("id") or label or f"t{index + 1}"),
        label=label,
        paths=paths,
        brand_paths=brands,
        query=query,
        enabled=bool(raw.get("enabled", True)),
    )


def _build_rule(raw: dict[str, Any], index: int) -> Rule:
    if not isinstance(raw, dict):
        raise ValueError(f"rules[{index}] باید یک شیء باشد")
    rule_id = str(raw.get("id") or f"rule-{index + 1}").strip()
    if not rule_id:
        raise ValueError(f"rules[{index}] شناسه خالی دارد")
    template = Rule(id=rule_id, name=str(raw.get("name") or rule_id))
    defaults = {f.name: getattr(template, f.name) for f in fields(Rule)}
    defaults["paths"] = list(defaults["paths"])
    defaults["target_ids"] = list(defaults["target_ids"])
    return Rule(**{**defaults, **_known(Rule, raw)})


def _parse_weights(data: Any) -> dict[str, float]:
    if not isinstance(data, dict) or not data:
        return ScoringConfig().weights
    out: dict[str, float] = {}
    for name, value in data.items():
        try:
            out[str(name)] = float(value)
        except (TypeError, ValueError):
            continue
    return out or ScoringConfig().weights


def load_config(path: str | Path | None = None) -> Config:
    config_path = Path(path) if path else Path.cwd() / DEFAULT_CONFIG_NAME
    if not config_path.is_file():
        raise FileNotFoundError(
            f"فایل تنظیمات پیدا نشد: {config_path}\n"
            f"یک کپی از config.example.json با نام {DEFAULT_CONFIG_NAME} بساز."
        )

    data = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("محتوای config.json باید یک شیء JSON باشد")

    scoring_raw = _section(data, "scoring")
    scoring = ScoringConfig(
        weights=_parse_weights(scoring_raw.get("weights")),
        market_max_age_hours=int(scoring_raw.get("market_max_age_hours", 96)),
        reference_year=int(scoring_raw.get("reference_year", 1404)),
        reference_mileage_km=int(scoring_raw.get("reference_mileage_km", 60_000)),
    )
    if scoring_raw.get("weights") and abs(sum(scoring.weights.values()) - 1.0) > 0.05:
        raise ValueError(
            f"مجموع وزن‌های امتیازدهی باید حدود ۱ باشد، فعلی: {sum(scoring.weights.values()):.2f}"
        )

    targets_raw = data.get("targets")
    if targets_raw is None:
        targets_raw = []
    if not isinstance(targets_raw, list):
        raise ValueError("کلید targets باید یک آرایه باشد")
    targets = [_build_target(raw, i) for i, raw in enumerate(targets_raw)]

    rules_raw = data.get("rules") or []
    if not isinstance(rules_raw, list):
        raise ValueError("کلید rules باید یک آرایه باشد")

    seen: set[str] = set()
    rules: list[Rule] = []
    for index, raw in enumerate(rules_raw):
        rule = _build_rule(raw, index)
        if rule.id in seen:
            raise ValueError(f"شناسهٔ تکراری در rules: {rule.id}")
        seen.add(rule.id)
        rules.append(rule)

    config = Config(
        search=SearchConfig(**_known(SearchConfig, _section(data, "search"))),
        watch=WatchConfig(**_known(WatchConfig, _section(data, "watch"))),
        targets=targets,
        filters=FilterConfig(**_known(FilterConfig, _section(data, "filters"))),
        deals=DealConfig(**_known(DealConfig, _section(data, "deals"))),
        rules=rules,
        scoring=scoring,
        notify=NotifyConfig(**_known(NotifyConfig, _section(data, "notify"))),
        storage=StorageConfig(**_known(StorageConfig, _section(data, "storage"))),
        _config_dir=config_path.resolve().parent,
    )

    _validate(config)
    return config


def _validate(config: Config) -> None:
    if not config.search.city.strip():
        raise ValueError("search.city نمی‌تواند خالی باشد")
    if not str(config.search.city_id).strip():
        raise ValueError("search.city_id نمی‌تواند خالی باشد")
    if config.search.pages < 1:
        raise ValueError("search.pages باید حداقل ۱ باشد")
    if config.watch.delay_min_seconds < 0 or config.watch.delay_max_seconds < 0:
        raise ValueError("watch delay نمی‌تواند منفی باشد")
    if config.watch.delay_max_seconds < config.watch.delay_min_seconds:
        raise ValueError("watch.delay_max_seconds باید بزرگ‌تر یا مساوی delay_min باشد")

    filters = config.filters
    if (
        filters.min_price_toman
        and filters.max_price_toman
        and filters.min_price_toman > filters.max_price_toman
    ):
        raise ValueError("filters.min_price_toman بزرگ‌تر از max است")
    if filters.min_year and filters.max_year and filters.min_year > filters.max_year:
        raise ValueError("filters.min_year بزرگ‌تر از max است")

    deals = config.deals
    if deals.min_profit_toman < 0:
        raise ValueError("deals.min_profit_toman نمی‌تواند منفی باشد")
    if deals.max_age_hours < 0:
        raise ValueError("deals.max_age_hours نمی‌تواند منفی باشد")
    if not 0 <= deals.min_roi < 1:
        raise ValueError("deals.min_roi باید بین ۰ و ۱ باشد")
    if deals.min_year and deals.max_year and deals.min_year > deals.max_year:
        raise ValueError("deals.min_year بزرگ‌تر از max است")

    target_ids = {t.id for t in config.targets}
    if len(target_ids) != len(config.targets):
        raise ValueError("شناسهٔ تکراری در targets")

    for rule in config.rules:
        if (
            rule.min_price_toman
            and rule.max_price_toman
            and rule.min_price_toman > rule.max_price_toman
        ):
            raise ValueError(f"بازهٔ قیمت در rule «{rule.name}» نامعتبر است")
        if rule.max_discount_vs_market is not None and not 0 < rule.max_discount_vs_market < 1:
            raise ValueError(f"max_discount_vs_market در rule «{rule.name}» باید بین ۰ و ۱ باشد")
        for target_id in rule.target_ids:
            if target_ids and target_id not in target_ids:
                raise ValueError(f"rule «{rule.name}» به target ناموجود اشاره می‌کند: {target_id}")
