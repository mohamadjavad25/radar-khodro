"""SQLite persistence for listings, price history and run state."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .models import Listing, Price, utcnow

SCHEMA_VERSION = 3

_SPEC_COLUMNS = {
    "paint": "TEXT",
    "chassis": "TEXT",
    "body_condition": "TEXT",
    "engine": "TEXT",
    "gearbox": "TEXT",
    "gearbox_state": "TEXT",
    "fuel": "TEXT",
    "insurance": "TEXT",
    "trim": "TEXT",
    "body_type": "TEXT",
    "published_at": "TEXT",
    "published_text": "TEXT",
    "bookmarked": "INTEGER NOT NULL DEFAULT 0",
    "dismissed": "INTEGER NOT NULL DEFAULT 0",
    "noted_at": "TEXT",
    "specs_fetched": "INTEGER NOT NULL DEFAULT 0",
    "margin_toman": "INTEGER",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS listings (
    listing_id       TEXT PRIMARY KEY,
    title            TEXT NOT NULL DEFAULT '',
    description      TEXT NOT NULL DEFAULT '',
    url              TEXT,
    vehicle_path     TEXT,
    vehicle_name     TEXT,
    vehicle_fa       TEXT,
    brand_path       TEXT,
    brand_fa         TEXT,
    year             INTEGER,
    mileage_km       INTEGER,
    city             TEXT,
    district         TEXT,
    business         INTEGER NOT NULL DEFAULT 0,
    price_toman      INTEGER,
    price_raw        TEXT NOT NULL DEFAULT '',
    negotiable       INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT,
    published_at     TEXT,
    published_text   TEXT,
    paint            TEXT,
    chassis          TEXT,
    body_condition   TEXT,
    engine           TEXT,
    gearbox          TEXT,
    gearbox_state    TEXT,
    fuel             TEXT,
    insurance        TEXT,
    trim             TEXT,
    body_type        TEXT,
    specs_fetched    INTEGER NOT NULL DEFAULT 0,
    bookmarked       INTEGER NOT NULL DEFAULT 0,
    dismissed        INTEGER NOT NULL DEFAULT 0,
    noted_at         TEXT,
    first_seen_at    TEXT NOT NULL,
    last_seen_at     TEXT NOT NULL,
    score            REAL,
    margin_toman     INTEGER,
    raw              TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_listings_vehicle ON listings(vehicle_path);
CREATE INDEX IF NOT EXISTS idx_listings_brand ON listings(brand_path);
CREATE INDEX IF NOT EXISTS idx_listings_price ON listings(price_toman);
CREATE INDEX IF NOT EXISTS idx_listings_seen ON listings(last_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_bookmarked ON listings(bookmarked);
CREATE INDEX IF NOT EXISTS idx_listings_dismissed ON listings(dismissed);
CREATE INDEX IF NOT EXISTS idx_listings_published ON listings(published_at);

CREATE TABLE IF NOT EXISTS price_history (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   TEXT NOT NULL,
    price_toman  INTEGER,
    observed_at  TEXT NOT NULL,
    FOREIGN KEY (listing_id) REFERENCES listings(listing_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_history_listing ON price_history(listing_id, observed_at);

CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    listing_id   TEXT NOT NULL,
    rule_id      TEXT NOT NULL,
    score        REAL,
    sent_at      TEXT NOT NULL,
    UNIQUE (listing_id, rule_id)
);

CREATE TABLE IF NOT EXISTS runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at   TEXT NOT NULL,
    finished_at  TEXT,
    fetched      INTEGER NOT NULL DEFAULT 0,
    new_items    INTEGER NOT NULL DEFAULT 0,
    notified     INTEGER NOT NULL DEFAULT 0,
    status       TEXT NOT NULL DEFAULT 'ok',
    message      TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""

_LISTING_COLUMNS = (
    "listing_id, title, description, url, vehicle_path, vehicle_name, vehicle_fa, "
    "brand_path, brand_fa, year, mileage_km, city, district, business, price_toman, "
    "price_raw, negotiable, created_at, published_at, published_text, paint, chassis, "
    "body_condition, engine, gearbox, gearbox_state, fuel, insurance, trim, body_type, "
    "specs_fetched, first_seen_at, last_seen_at, score, margin_toman, raw"
)
_PLACEHOLDERS = ",".join("?" * len(_LISTING_COLUMNS.split(",")))


@dataclass(slots=True)
class UpsertResult:
    listing: Listing
    is_new: bool
    old_price: int | None
    price_changed: bool


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path, timeout=30, isolation_level=None, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._prepare_existing_tables()
        self._conn.executescript(_SCHEMA)
        self.set_meta("schema_version", str(SCHEMA_VERSION))

    def _prepare_existing_tables(self) -> None:
        """Add newer columns to a database created by an older version."""
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='listings'"
        ).fetchone()
        if row is None:
            return
        existing = {r["name"] for r in self._conn.execute("PRAGMA table_info(listings)")}
        for column, kind in _SPEC_COLUMNS.items():
            if column not in existing:
                self._conn.execute(f"ALTER TABLE listings ADD COLUMN {column} {kind}")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(sql, params)

    def executemany(self, sql: str, seq: Iterable[Iterable[Any]]) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(sql, seq)

    def set_meta(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_meta(self, key: str, default: str | None = None) -> str | None:
        row = self.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def upsert(self, listing: Listing) -> UpsertResult:
        row = self.execute(
            "SELECT price_toman, first_seen_at FROM listings WHERE listing_id=?",
            (listing.listing_id,),
        ).fetchone()
        is_new = row is None
        old_price = row["price_toman"] if row else None

        if row:
            listing.first_seen_at = _parse_dt(row["first_seen_at"]) or listing.first_seen_at
        listing.last_seen_at = utcnow()

        price_changed = (
            not is_new
            and listing.price.toman is not None
            and old_price is not None
            and old_price != listing.price.toman
        )

        self.execute(
            f"""
            INSERT INTO listings ({_LISTING_COLUMNS}) VALUES ({_PLACEHOLDERS})
            ON CONFLICT(listing_id) DO UPDATE SET
                title=excluded.title,
                description=excluded.description,
                url=excluded.url,
                vehicle_path=excluded.vehicle_path,
                vehicle_name=excluded.vehicle_name,
                vehicle_fa=excluded.vehicle_fa,
                brand_path=excluded.brand_path,
                brand_fa=excluded.brand_fa,
                year=excluded.year,
                mileage_km=excluded.mileage_km,
                city=excluded.city,
                district=excluded.district,
                business=excluded.business,
                price_toman=excluded.price_toman,
                price_raw=excluded.price_raw,
                negotiable=excluded.negotiable,
                created_at=excluded.created_at,
                published_at=COALESCE(excluded.published_at, listings.published_at),
                published_text=COALESCE(excluded.published_text, listings.published_text),
                paint=COALESCE(excluded.paint, listings.paint),
                chassis=COALESCE(excluded.chassis, listings.chassis),
                body_condition=COALESCE(excluded.body_condition, listings.body_condition),
                engine=COALESCE(excluded.engine, listings.engine),
                gearbox=COALESCE(excluded.gearbox, listings.gearbox),
                gearbox_state=COALESCE(excluded.gearbox_state, listings.gearbox_state),
                fuel=COALESCE(excluded.fuel, listings.fuel),
                insurance=COALESCE(excluded.insurance, listings.insurance),
                trim=COALESCE(excluded.trim, listings.trim),
                body_type=COALESCE(excluded.body_type, listings.body_type),
                specs_fetched=MAX(listings.specs_fetched, excluded.specs_fetched),
                last_seen_at=excluded.last_seen_at,
                score=excluded.score,
                margin_toman=excluded.margin_toman,
                raw=excluded.raw
            """,
            (
                listing.listing_id,
                listing.title,
                listing.description,
                listing.url,
                listing.vehicle_path,
                listing.vehicle_name,
                listing.vehicle_fa,
                listing.brand_path,
                listing.brand_fa,
                listing.year,
                listing.mileage_km,
                listing.city,
                listing.district,
                int(listing.business),
                listing.price.toman,
                listing.price.raw,
                int(listing.price.negotiable),
                _iso(listing.created_at),
                _iso(listing.published_at),
                listing.published_text,
                listing.paint,
                listing.chassis,
                listing.body_condition,
                listing.engine,
                listing.gearbox,
                listing.gearbox_state,
                listing.fuel,
                listing.insurance,
                listing.trim,
                listing.body_type,
                int(listing.specs_fetched),
                _iso(listing.first_seen_at),
                _iso(listing.last_seen_at),
                None,
                listing.margin_toman,
                json.dumps(listing.raw, ensure_ascii=False),
            ),
        )

        if is_new or price_changed:
            self.execute(
                "INSERT INTO price_history(listing_id, price_toman, observed_at) VALUES(?,?,?)",
                (listing.listing_id, listing.price.toman, _iso(utcnow())),
            )

        return UpsertResult(listing, is_new, old_price, price_changed)

    def upsert_many(self, listings: Iterable[Listing]) -> list[UpsertResult]:
        return [self.upsert(listing) for listing in listings]

    def set_score(self, listing_id: str, score: float | None) -> None:
        self.execute("UPDATE listings SET score=? WHERE listing_id=?", (score, listing_id))

    def set_scores(self, scores: dict[str, float]) -> None:
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE listings SET score=? WHERE listing_id=?",
                [(score, listing_id) for listing_id, score in scores.items()],
            )

    def get(self, listing_id: str) -> Listing | None:
        row = self.execute(
            "SELECT * FROM listings WHERE listing_id=?", (listing_id,)
        ).fetchone()
        return _row_to_listing(row) if row else None

    def query(
        self,
        *,
        vehicle_paths: Iterable[str] | None = None,
        brand_paths: Iterable[str] | None = None,
        city: str | None = None,
        min_year: int | None = None,
        max_year: int | None = None,
        min_price_toman: int | None = None,
        max_price_toman: int | None = None,
        max_mileage_km: int | None = None,
        min_mileage_km: int | None = None,
        chassis: str | None = None,
        body_condition: str | None = None,
        exclude_business: bool = False,
        bookmarked: bool | None = None,
        dismissed: bool | None = None,
        max_published_age_hours: int | None = None,
        min_margin_toman: int | None = None,
        limit: int | None = None,
        since: datetime | None = None,
    ) -> list[Listing]:
        clauses: list[str] = []
        params: list[Any] = []

        paths = [p for p in (vehicle_paths or []) if p]
        if paths:
            group: list[str] = []
            for path in paths:
                group.append("vehicle_path = ?")
                params.append(path)
                group.append("vehicle_path LIKE ?")
                params.append(path + "/%")
            clauses.append("(" + " OR ".join(group) + ")")

        brands = [b for b in (brand_paths or []) if b]
        if brands:
            clauses.append(f"brand_path IN ({','.join('?' * len(brands))})")
            params.extend(brands)

        if city:
            clauses.append("city = ?")
            params.append(city)
        if min_year is not None:
            clauses.append("year >= ?")
            params.append(min_year)
        if max_year is not None:
            clauses.append("year <= ?")
            params.append(max_year)
        if min_price_toman is not None:
            clauses.append("price_toman >= ?")
            params.append(min_price_toman)
        if max_price_toman is not None:
            clauses.append("price_toman <= ?")
            params.append(max_price_toman)
        if max_mileage_km is not None:
            clauses.append("mileage_km <= ?")
            params.append(max_mileage_km)
        if min_mileage_km is not None:
            clauses.append("mileage_km >= ?")
            params.append(min_mileage_km)
        if chassis:
            clauses.append("chassis = ?")
            params.append(chassis)
        if body_condition:
            clauses.append("body_condition = ?")
            params.append(body_condition)
        if exclude_business:
            clauses.append("business = 0")
        if bookmarked is not None:
            clauses.append("bookmarked = ?")
            params.append(int(bookmarked))
        if dismissed is not None:
            clauses.append("dismissed = ?")
            params.append(int(dismissed))
        if min_margin_toman is not None:
            clauses.append("margin_toman >= ?")
            params.append(min_margin_toman)
        if max_published_age_hours is not None:
            cutoff = utcnow() - timedelta(hours=max_published_age_hours)
            clauses.append("(published_at IS NOT NULL AND published_at >= ?)")
            params.append(_iso(cutoff))
        if since is not None:
            clauses.append("first_seen_at >= ?")
            params.append(_iso(since))

        sql = "SELECT * FROM listings"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY first_seen_at DESC"
        if limit:
            sql += " LIMIT ?"
            params.append(limit)

        return [_row_to_listing(row) for row in self.execute(sql, params)]

    def set_bookmark(self, listing_id: str, value: bool) -> bool:
        cursor = self.execute(
            "UPDATE listings SET bookmarked=?, noted_at=? WHERE listing_id=?",
            (int(value), _iso(utcnow()), listing_id),
        )
        return bool(cursor.rowcount)

    def set_dismissed(self, listing_id: str, value: bool) -> bool:
        cursor = self.execute(
            "UPDATE listings SET dismissed=? WHERE listing_id=?", (int(value), listing_id)
        )
        return bool(cursor.rowcount)

    def set_margin(self, margins: dict[str, int]) -> None:
        if not margins:
            return
        with self.transaction() as conn:
            conn.executemany(
                "UPDATE listings SET margin_toman=? WHERE listing_id=?",
                [(margin, listing_id) for listing_id, margin in margins.items()],
            )

    def bookmarked(self, limit: int = 500) -> list[Listing]:
        return self.query(bookmarked=True, dismissed=False, limit=limit)

    def count_bookmarked(self) -> int:
        row = self.execute(
            "SELECT COUNT(*) AS n FROM listings WHERE bookmarked=1 AND dismissed=0"
        ).fetchone()
        return int(row["n"]) if row else 0

    def needs_specs(self, limit: int = 30) -> list[str]:
        rows = self.execute(
            "SELECT listing_id FROM listings WHERE specs_fetched=0 AND dismissed=0 "
            "ORDER BY first_seen_at DESC LIMIT ?",
            (limit,),
        )
        return [row["listing_id"] for row in rows]

    def mark_specs_fetched(self, listing_id: str) -> None:
        self.execute(
            "UPDATE listings SET specs_fetched=1 WHERE listing_id=?", (listing_id,)
        )

    def price_history(self, listing_id: str) -> list[tuple[datetime, int | None]]:
        rows = self.execute(
            "SELECT observed_at, price_toman FROM price_history "
            "WHERE listing_id=? ORDER BY observed_at",
            (listing_id,),
        )
        return [
            (observed, price)
            for observed, price in ((_parse_dt(r["observed_at"]), r["price_toman"]) for r in rows)
        ]

    def already_notified(self, listing_id: str, rule_id: str) -> bool:
        row = self.execute(
            "SELECT 1 FROM notifications WHERE listing_id=? AND rule_id=?",
            (listing_id, rule_id),
        ).fetchone()
        return row is not None

    def mark_notified(self, listing_id: str, rule_id: str, score: float | None) -> None:
        self.execute(
            "INSERT OR IGNORE INTO notifications(listing_id, rule_id, score, sent_at) "
            "VALUES(?,?,?,?)",
            (listing_id, rule_id, score, _iso(utcnow())),
        )

    def recent_price_drops(self, limit: int = 20) -> list[Listing]:
        rows = self.execute(
            "SELECT listing_id, price_toman FROM price_history "
            "WHERE price_toman IS NOT NULL "
            "ORDER BY listing_id, observed_at"
        )

        latest: dict[str, int] = {}
        drops: list[tuple[float, str]] = []
        for row in rows:
            listing_id = row["listing_id"]
            current = latest.get(listing_id)
            price = int(row["price_toman"])
            if current and price < current:
                drops.append(((current - price) / current, listing_id))
            latest[listing_id] = price

        drops.sort(reverse=True)
        listings: list[Listing] = []
        for _, listing_id in drops[:limit]:
            listing = self.get(listing_id)
            if listing:
                listings.append(listing)
        return listings

    def start_run(self) -> int:
        cursor = self.execute("INSERT INTO runs(started_at) VALUES(?)", (_iso(utcnow()),))
        return int(cursor.lastrowid or 0)

    def finish_run(
        self,
        run_id: int,
        *,
        fetched: int = 0,
        new_items: int = 0,
        notified: int = 0,
        status: str = "ok",
        message: str = "",
    ) -> None:
        self.execute(
            "UPDATE runs SET finished_at=?, fetched=?, new_items=?, notified=?, status=?, message=? "
            "WHERE id=?",
            (_iso(utcnow()), fetched, new_items, notified, status, message[:500], run_id),
        )

    def purge_older_than(self, days: int) -> int:
        cutoff = utcnow().timestamp() - days * 86_400
        cursor = self.execute(
            "DELETE FROM listings WHERE last_seen_at < ?",
            (_iso(datetime.fromtimestamp(cutoff, tz=utcnow().tzinfo)),),
        )
        return cursor.rowcount or 0


def _row_to_listing(row: sqlite3.Row) -> Listing:
    keys = set(row.keys())

    def val(name: str, default: Any = None) -> Any:
        return row[name] if name in keys else default

    return Listing(
        listing_id=row["listing_id"],
        title=row["title"],
        description=row["description"],
        price=Price(
            toman=row["price_toman"],
            negotiable=bool(row["negotiable"]),
            raw=row["price_raw"],
        ),
        vehicle_path=val("vehicle_path"),
        vehicle_name=val("vehicle_name"),
        vehicle_fa=val("vehicle_fa"),
        brand_path=val("brand_path"),
        brand_fa=val("brand_fa"),
        year=row["year"],
        mileage_km=row["mileage_km"],
        city=row["city"],
        district=row["district"],
        url=row["url"],
        business=bool(row["business"]),
        published_at=_parse_dt(val("published_at")),
        published_text=val("published_text"),
        paint=val("paint"),
        chassis=val("chassis"),
        body_condition=val("body_condition"),
        engine=val("engine"),
        gearbox=val("gearbox"),
        gearbox_state=val("gearbox_state"),
        fuel=val("fuel"),
        insurance=val("insurance"),
        trim=val("trim"),
        body_type=val("body_type"),
        specs_fetched=bool(val("specs_fetched", 0)),
        bookmarked=bool(val("bookmarked", 0)),
        dismissed=bool(val("dismissed", 0)),
        noted_at=_parse_dt(val("noted_at")),
        created_at=_parse_dt(row["created_at"]),
        first_seen_at=_parse_dt(row["first_seen_at"]) or utcnow(),
        last_seen_at=_parse_dt(row["last_seen_at"]) or utcnow(),
        score=row["score"],
        margin_toman=val("margin_toman"),
        raw=json.loads(row["raw"] or "{}"),
    )
