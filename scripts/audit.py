"""Inspect the live database for parsing quality problems."""

import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pride_radar.db import Database
from pride_radar import text as t

db = Database(ROOT / "data" / "pride.db")
items = db.query(limit=5000)

print(f"total listings      : {len(items)}")
print(f"with model          : {sum(1 for i in items if i.model)}")
print(f"with year           : {sum(1 for i in items if i.year)}")
print(f"with mileage        : {sum(1 for i in items if i.mileage_km)}")
print(f"with price          : {sum(1 for i in items if i.price.toman)}")
print(f"flagged business    : {sum(1 for i in items if i.business)}")
print(f"negotiable only     : {sum(1 for i in items if i.price.negotiable and not i.price.toman)}")

print("\nmodel distribution  :", Counter(str(i.model) for i in items))
print("year range          :", min((i.year for i in items if i.year), default=None), "-", max((i.year for i in items if i.year), default=None))
prices = sorted(i.price.toman for i in items if i.price.toman)
print("price p10/med/p90   :", t.format_toman(prices[len(prices)//10]), "/", t.format_toman(prices[len(prices)//2]), "/", t.format_toman(prices[len(prices)*9//10]))

print("\n--- suspicious prices (< 50M) ---")
for i in items:
    if i.price.toman and i.price.toman < 50_000_000:
        print(f"  {i.price.toman:>12,} | {i.title[:44]} | raw={i.price.raw!r}")

print("\n--- suspicious mileage (< 1000) ---")
for i in items:
    if i.mileage_km is not None and i.mileage_km < 1000:
        print(f"  {i.mileage_km:>8,} | {i.title[:44]} | raw={i.raw.get('top_description_text')!r}")

print("\n--- titles without year that look like they have one ---")
for i in items:
    if not i.year and i.title:
        digits = t.iter_numbers(i.title)
        if digits:
            print(f"  {i.title[:50]:52} -> {digits[:4]}")

print("\n--- no model detected ---")
for i in items:
    if not i.model:
        print(f"  {i.title[:60]}")
db.close()
