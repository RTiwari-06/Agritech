"""Migrate the legacy ``agritech`` collection into ``market_prices``.

The ``agritech`` docs (written by ``scripts/load_agmarknet_data.py``) use a
flattened schema:

    commodity, commodity_group, market, arrival_date, msp_rs_per_quintal,
    market_price_rs_per_quintal, market_price_rs_per_kg,
    arrival_metric_tonnes, trend, vs_msp_pct, source, synthetic, ...

Migration maps them onto the canonical ``market_prices`` schema:

    commodity, commodity_group, market, market_date, price_per_kg,
    msp_per_kg (-/100), arrival_metric_tonnes, synthetic, source,
    report_generated_at, raw_text, parse_error

Idempotent: inserts are deduped on (commodity, market, market_date) via the
service layer; synthetic rows yield to real rows.

Usage:
    python scripts/migrate_market_data.py [--dry-run]
"""

import sys
from datetime import datetime, timezone

sys.path.insert(0, ".")

from market_data import ingest_records  # noqa: E402
from database.db import get_db, session_scope  # noqa: E402


def to_float(value):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def build_records(docs):
    records = []
    for d in docs:
        market_date = d.get("arrival_date") or d.get("market_date")
        if not market_date:
            market_date = (d.get("report_generated_at") or "")[:10]
        if not market_date:
            continue
        price_kg = to_float(d.get("market_price_rs_per_kg"))
        price_quintal = to_float(d.get("market_price_rs_per_quintal"))
        msp_quintal = to_float(d.get("msp_rs_per_quintal"))
        msp_kg = to_float(d.get("msp_per_kg"))
        records.append({
            "commodity": d.get("commodity"),
            "commodity_group": d.get("commodity_group") or "General",
            "market": d.get("market") or "National Aggregate",
            "market_date": market_date,
            "price_per_kg": price_kg if price_kg is not None else (
                price_quintal / 100.0 if price_quintal is not None else None
            ),
            "msp_per_kg": msp_kg if msp_kg is not None else (
                msp_quintal / 100.0 if msp_quintal is not None else None
            ),
            "arrival_metric_tonnes": to_float(d.get("arrival_metric_tonnes")),
            "synthetic": bool(d.get("synthetic", False)),
            "source": d.get("source") or "agmarknet.gov.in (legacy agritech)",
            "report_generated_at": d.get("report_generated_at"),
            "raw_text": d.get("raw_text") or f"migrated from agritech#{d.get('_id')}",
            "parse_error": None,
        })
    return records


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    db = get_db()
    docs = list(db["agritech"].find({}))
    print(f"Legacy 'agritech' docs found: {len(docs)}")
    records = build_records(docs)
    print(f"Canonical records built: {len(records)}")
    if dry_run:
        print("Dry-run — no writes performed.")
        return 0

    result = ingest_records(records, source="agritech collection migration")
    print("Ingest result:", result)
    with session_scope() as s:
        total = s["market_prices"].count_documents({})
        real = s["market_prices"].count_documents({"synthetic": False})
        dupes = len(records) - result["inserted"] - result["updated"] - result["errors"]
    print(
        f"market_prices now: {total} total ({real} real; "
        f"{dupes} duplicate keys skipped)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())