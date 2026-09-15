"""Load Agmarknet market price & arrival data into MongoDB.

Parses the 2 PDF reports (12 Sep + 13 Sep 2026) and generates synthetic
data for the 5 preceding days (08-12 Sep 2026) so the codebase has a full
week of market data to work with.

Schema (agritech collection) — designed to fill the codebase gap:
  - market_price_rs_per_kg : maps to product.price for benchmark comparison
  - price_change_pct       : feeds the dynamic pricing engine's trend signal
  - vs_msp_pct             : lets the system flag below-MSP commodities
  - trend                  : rising/falling/stable — semantic signal for NLP+optimisation
  - arrival_metric_tonnes  : demand/absorption signal for the forecasting engine
  - msp_rs_per_quintal     : farmer-seller decision support (is my price fair?)
"""

import json
import random
import sys
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# 1. Raw data from the 2 Agmarknet PDF reports (12 Sep + 13 Sep 2026)
# ---------------------------------------------------------------------------

DATA_13_SEP = [
    ("Cereals",   "Bajra (Pearl Millet/Cumbu)",    2900.00, 2241.55,  1266.08),
    ("Cereals",   "Barley (Jau)",                   2150.00, 2464.54,   177.64),
    ("Cereals",   "Jowar (Sorghum)",                4023.00, 2435.78,   243.82),
    ("Cereals",   "Maize",                          2410.00, 2154.91, 13498.25),
    ("Cereals",   "Paddy (Common)",                 2441.00, 3067.99, 13506.99),
    ("Cereals",   "Ragi (Finger Millet)",           5205.00, 3423.08,    65.00),
    ("Cereals",   "Wheat",                          2585.00, 2543.16, 13313.14),
    ("Fibre Crops","Cotton",                        8267.00, 8115.57,   188.64),
    ("Oil Seeds", "Copra",                         12100.00,22779.41,    47.60),
    ("Oil Seeds", "Groundnut",                       7517.00, 6267.10,   224.12),
    ("Oil Seeds", "Mustard",                         6200.00, 7241.83,   797.82),
    ("Oil Seeds", "Safflower",                       6540.00,    None,      None),
    ("Oil Seeds", "Sesamum (Sesame, Gingelly, Til)",10346.00,11632.29,    40.37),
    ("Oil Seeds", "Soyabean",                        5708.00, 5628.01,  1002.66),
    ("Oil Seeds", "Sunflower/Sunflower Seed",        8343.00, 7471.00,   202.10),
    ("Pulses",    "Bengal Gram (Gram) (Whole)",      5875.00, 6059.97,   167.15),
    ("Pulses",    "Black Gram (Urd Beans) (Whole)",  8200.00, 8075.19,   309.28),
    ("Pulses",    "Green Gram (Moong) (Whole)",      8780.00, 7421.06,  1383.20),
    ("Pulses",    "Lentil (Masur) (Whole)",          7000.00, 6073.01,    46.38),
    ("Pulses",    "Red gram/Arhar/Tur (whole)",      8450.00, 8905.92,   246.21),
    ("Vegetables","Onion",                             None, 4244.71,  7309.34),
    ("Vegetables","Potato",                            None,  623.01, 27216.64),
    ("Vegetables","Tomato",                            None, 1679.02,  4485.98),
]

DATA_12_SEP = [
    ("Cereals",   "Bajra (Pearl Millet/Cumbu)",    2900.00, 2219.15,  1267.83),
    ("Cereals",   "Barley (Jau)",                   2150.00, 2392.70,   220.59),
    ("Cereals",   "Jowar (Sorghum)",                4023.00, 5150.21,   148.30),
    ("Cereals",   "Maize",                          2410.00, 2122.87, 16453.98),
    ("Cereals",   "Paddy (Common)",                 2441.00, 2911.26, 12493.20),
    ("Cereals",   "Ragi (Finger Millet)",           5205.00, 5550.00,     0.30),
    ("Cereals",   "Wheat",                          2585.00, 2600.87, 26109.03),
    ("Fibre Crops","Cotton",                        8267.00, 8911.08,  2136.09),
    ("Oil Seeds", "Copra",                         12100.00,16807.29,    19.20),
    ("Oil Seeds", "Groundnut",                       7517.00, 7040.33,   666.61),
    ("Oil Seeds", "Mustard",                         6200.00, 7301.47,  2118.89),
    ("Oil Seeds", "Niger Seed (Ramtil)",            10052.00,    None,      None),
    ("Oil Seeds", "Safflower",                       6540.00, 4917.90,    14.10),
    ("Oil Seeds", "Sesamum (Sesame, Gingelly, Til)",10346.00,11840.43,   202.33),
    ("Oil Seeds", "Soyabean",                        5708.00, 5814.82,   907.51),
    ("Oil Seeds", "Sunflower/Sunflower Seed",        8343.00, 7262.06,    25.30),
    ("Pulses",    "Bengal Gram (Gram) (Whole)",      5875.00, 6349.73,  1162.63),
    ("Pulses",    "Black Gram (Urd Beans) (Whole)",  8200.00, 7876.82,   968.23),
    ("Pulses",    "Green Gram (Moong) (Whole)",      8780.00, 7756.69,  4712.47),
    ("Pulses",    "Lentil (Masur) (Whole)",          7000.00, 8368.09,   341.00),
    ("Pulses",    "Red gram/Arhar/Tur (whole)",      8450.00, 8559.47,  1247.74),
    ("Vegetables","Onion",                             None, 3669.51,  9207.94),
    ("Vegetables","Potato",                            None, 631.71,  30480.52),
    ("Vegetables","Tomato",                            None, 1742.54,  5351.96),
]


# ---------------------------------------------------------------------------
# 2. Synthetic data generator for the 5 preceding days (08-12 Sep 2026)
# ---------------------------------------------------------------------------

def synthetic_row(group, commodity, msp, base_price, base_arrival, date):
    noise = random.gauss(0, 0.025)
    price = base_price * (1.0 + noise) if base_price else None
    if msp is not None and price is not None:
        price = max(price, msp * 0.85)
        price = min(price, msp * 1.8)

    arrival = base_arrival * (1.0 + random.gauss(0, 0.12)) if base_arrival else None
    arrival = max(arrival, 0.1) if arrival else None

    price_change_pct = ((price - base_price) / base_price * 100.0) if (base_price and price) else None
    vs_msp_pct = ((price - msp) / msp * 100.0) if (msp and price) else None

    if price_change_pct is None:
        trend = "unknown"
    elif price_change_pct > 3:
        trend = "rising"
    elif price_change_pct < -3:
        trend = "falling"
    else:
        trend = "stable"

    return {
        "commodity": commodity,
        "commodity_group": group,
        "market": "National Aggregate",
        "arrival_date": date.isoformat(),
        "msp_rs_per_quintal": msp,
        "market_price_rs_per_quintal": round(price, 2) if price else None,
        "market_price_rs_per_kg": round(price / 100.0, 4) if price else None,
        "arrival_metric_tonnes": round(arrival, 2) if arrival else None,
        "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
        "vs_msp_pct": round(vs_msp_pct, 2) if vs_msp_pct is not None else None,
        "trend": trend,
        "source": "agmarknet.gov.in",
        "report_generated_at": "2026-09-16T02:20:00Z",
        "synthetic": True,
    }


def generate_synthetic_week():
    random.seed(42)
    rows = []
    anchor_date = datetime(2026, 9, 13, tzinfo=timezone.utc)

    for group, commodity, msp, price_13, arrival_13 in DATA_13_SEP:
        prev_price = price_13
        prev_arrival = arrival_13

        for day_offset in range(1, 6):
            date = anchor_date - timedelta(days=day_offset)
            row = synthetic_row(group, commodity, msp, prev_price, prev_arrival, date)
            rows.append(row)
            prev_price = row["market_price_rs_per_quintal"]
            prev_arrival = row["arrival_metric_tonnes"]

    return rows


# ---------------------------------------------------------------------------
# 3. Parse the real PDF data
# ---------------------------------------------------------------------------

def parse_real_report(data, report_date_str, generated_at):
    rows = []
    for group, commodity, msp, price, arrival in data:
        if price is None and arrival is None:
            continue
        vs_msp_pct = ((price - msp) / msp * 100.0) if (msp and price) else None
        price_change_pct = None

        if price_change_pct is None and len(rows) > 0:
            for prev in reversed(rows):
                if prev["commodity"] == commodity:
                    pp = prev["market_price_rs_per_quintal"]
                    if pp and price:
                        price_change_pct = (price - pp) / pp * 100.0
                    break

        trend = "unknown"
        if price_change_pct is not None:
            if price_change_pct > 3:
                trend = "rising"
            elif price_change_pct < -3:
                trend = "falling"
            else:
                trend = "stable"

        row = {
            "commodity": commodity,
            "commodity_group": group,
            "market": "National Aggregate",
            "arrival_date": report_date_str,
            "msp_rs_per_quintal": msp,
            "market_price_rs_per_quintal": price,
            "market_price_rs_per_kg": round(price / 100.0, 4) if price else None,
            "arrival_metric_tonnes": arrival,
            "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
            "vs_msp_pct": round(vs_msp_pct, 2) if vs_msp_pct is not None else None,
            "trend": trend,
            "source": "agmarknet.gov.in",
            "report_generated_at": generated_at,
            "synthetic": False,
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# 4. Load into MongoDB
# ---------------------------------------------------------------------------

def load_into_mongodb(rows):
    from database.db import get_db, init_db, _next_id
    init_db()
    db = get_db()
    collection = db["agritech"]

    # Use the existing _next_id helper for integer _ids
    inserted = 0
    for row in rows:
        doc = dict(row)
        doc["_id"] = _next_id(db)
        doc.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        collection.insert_one(doc)
        inserted += 1

    total = collection.count_documents({})
    print(f"Inserted into 'agritech': {inserted} new records (total: {total})")
    return inserted


def show_summary(rows):
    print(f"Total records generated: {len(rows)}")
    by_date = {}
    for r in rows:
        d = r["arrival_date"]
        by_date.setdefault(d, []).append(r)
    for date in sorted(by_date.keys()):
        recs = by_date[date]
        real_synth = "real" if not recs[0]["synthetic"] else "synthetic"
        print(f"  {date} ({real_synth}): {len(recs)} records, "
              f"{len([r for r in recs if r['market_price_rs_per_kg'] is not None])} with price/kg")
    print()
    print("=== Sample records ===")
    for r in rows[:2]:
        print(json.dumps(r, indent=2))
    print("...")
    for r in rows[-2:]:
        print(json.dumps(r, indent=2))


if __name__ == "__main__":
    rows = []
    real_13 = parse_real_report(DATA_13_SEP, "2026-09-13", "2026-09-16T02:20:00Z")
    real_12 = parse_real_report(DATA_12_SEP, "2026-09-12", "2026-09-14T07:41:00Z")
    synthetic = generate_synthetic_week()
    rows = real_13 + real_12 + synthetic

    show_summary(rows)

    print()
    print("=== Loading into MongoDB ===")
    n = load_into_mongodb(rows)
    print(f"Done — {n} records loaded into 'agritech' collection")

    # Verify
    from database.db import get_db
    db = get_db()
    coll = db["agritech"]
    print()
    print(f"Verification — 'agritech' collection: {coll.count_documents({})} total docs")
    doc = coll.find_one({"commodity": "Wheat", "arrival_date": "2026-09-13"})
    if doc:
        print("Sample Wheat 13 Sep (real):")
        print(json.dumps({k: doc[k] for k in sorted(doc.keys()) if k != "_id"}, indent=2, default=str))
    doc2 = coll.find_one({"commodity": "Wheat", "arrival_date": "2026-09-10"})
    if doc2:
        print("Sample Wheat 10 Sep (synthetic):")
        print(json.dumps({k: doc2[k] for k in sorted(doc2.keys()) if k != "_id"}, indent=2, default=str))
