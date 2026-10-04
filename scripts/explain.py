"""Explain why each listing does or does not match the configured rules."""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pride_radar.config import load_config
from pride_radar.db import Database
from pride_radar.market import build_snapshot, compare_to_market
from pride_radar.rules import check_rule, passes_global_filters

config = load_config(ROOT / "config.json")
db = Database(config.db_path)
items = db.query(limit=3000)
snapshot = build_snapshot(items)

print(f"listings: {len(items)}")
print(f"market sample: {snapshot.sample_size}  median={snapshot.overall.median:,}")

global_rejects = Counter()
print("\n--- global filter rejects ---")
for listing in items:
    reason = passes_global_filters(listing, config.filters)
    if reason:
        global_rejects[reason.reason] += 1
for reason, count in global_rejects.most_common():
    print(f"  {count:3}  {reason}")

print("\n--- per rule ---")
for rule in config.rules:
    rejects = Counter()
    hits = 0
    for listing in items:
        if passes_global_filters(listing, config.filters):
            continue
        verdict, reason = check_rule(listing, rule, snapshot)
        if verdict is not None and reason is None:
            hits += 1
        elif reason:
            rejects[reason.reason] += 1
    print(f"\n  [{rule.id}] hits={hits}")
    for reason, count in rejects.most_common(5):
        print(f"      {count:3}  {reason}")

print("\n--- closest candidates (131, min_year filter) ---")
shown = 0
for listing in sorted(items, key=lambda x: x.price.toman or 10**12):
    if listing.model != "131" or not listing.price.toman:
        continue
    verdict = compare_to_market(listing.price.toman, snapshot, listing.model, listing.year)
    print(
        f"  {listing.price.toman:>12,} | yr={str(listing.year):5} | km={str(listing.mileage_km):>7} "
        f"| baseline={str(verdict.baseline_median):>12} | {verdict.label:24} | {listing.title[:34]}"
    )
    shown += 1
    if shown >= 14:
        break
db.close()
