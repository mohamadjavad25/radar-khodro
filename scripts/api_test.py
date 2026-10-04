"""Exercise every web API endpoint end to end."""
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8765"


def call(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            body = r.read().decode("utf-8")
            return r.status, json.loads(body) if body.strip().startswith(("{", "[")) else len(body)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:200]
    except Exception as e:
        return -1, str(e)


def main():
    print("=== bootstrap ===")
    s, b = call("GET", "/api/bootstrap")
    if s != 200:
        print("  FAILED", s, b)
        return
    print("  version", b["version"], "| brands", len(b["catalog"]["brands"]),
          "| models", len(b["catalog"]["models"]), "| total", b["catalog"]["count"])
    print("  search:", b["config"]["search"])
    print("  stats :", b["stats"])
    print("  median:", b["market"]["overall"]["median"])

    print("\n=== HTML ===")
    s, b = call("GET", "/")
    print("  status", s, "| bytes", b if isinstance(b, int) else "?")

    print("\n=== filters ===")
    cases = {
        "all": "/api/listings?limit=5",
        "brand peugeot": "/api/listings?brands=peugeot&limit=5",
        "model 206": "/api/listings?paths=peugeot/206&limit=5",
        "model 131": "/api/listings?paths=pride/131&limit=5",
        "year 1395+": "/api/listings?min_year=1395&limit=5",
        "km<100k": "/api/listings?max_mileage=100000&limit=5",
        "price 300-700M": "/api/listings?min_price=300000000&max_price=700000000&limit=5",
        "discount 10%": "/api/listings?max_discount=0.10&limit=5",
        "score>80": "/api/listings?min_score=80&limit=5",
        "search text": "/api/listings?q=%D8%AF%D9%86%D8%A7&limit=5",
        "sort price_asc": "/api/listings?sort=price_asc&limit=3",
        "sort discount": "/api/listings?sort=discount&limit=3",
        "keep suspicious": "/api/listings?exclude_suspicious=0&sort=price_asc&limit=3",
        "multi model": "/api/listings?paths=peugeot/206,pride/131&limit=5",
    }
    for label, path in cases.items():
        s, b = call("GET", path)
        if s != 200:
            print(f"  {label:16} FAILED {s} {b}")
            continue
        items = b["items"]
        first = items[0] if items else None
        desc = (
            f"{first['label']} {first['year']} {first['price_text']} {first['label_market']}"
            if first else "-"
        )
        print(f"  {label:16} total={b['total']:3}  {desc}")

    print("\n=== detail ===")
    s, b = call("GET", "/api/listings?limit=1")
    if s == 200 and b["items"]:
        lid = b["items"][0]["id"]
        s2, d = call("GET", f"/api/listing/{lid}")
        print("  status", s2, "| offer", d.get("offer"), "| baseline", d.get("baseline"))

    print("\n=== market / stats / rules / catalog ===")
    s, m = call("GET", "/api/market")
    print("  market: sample", m.get("sample"), "vehicles", len(m.get("vehicles", {})),
          "brands", len(m.get("brands", {})))
    for path in ("/api/stats", "/api/rules", "/api/fetch/status", "/api/catalog"):
        s, b = call("GET", path)
        size = len(b) if isinstance(b, (dict, list)) else b
        print(f"  {path:20} status={s} size={size}")

    print("\n=== errors ===")
    print("  unknown route ->", call("GET", "/api/nope")[0])
    print("  bad listing   ->", call("GET", "/api/listing/zzz")[0])


if __name__ == "__main__":
    main()
