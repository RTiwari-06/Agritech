"""Market data service: ingest parsed records and query market prices.

Writes to the ``market_prices`` and ``market_ingest_log`` collections.
Dedup is guaranteed by the unique index ``(commodity, market, market_date)``;
on conflict a synthetic record yields to a real one.
"""

from datetime import datetime, timezone

from database.db import _next_id, session_scope


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def ingest_records(records, source=None, ingest_context=None):
    """Upsert parsed ``market_prices`` records (dedup by commodity+market+date).

    Returns counts: inserted, updated, skipped, plus a log entry written to
    ``market_ingest_log`` and any un-processable records.
    """
    result = {
        "inserted": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
        "error_rows": [],
        "total_parsed": len(records or []),
    }
    with session_scope() as s:
        for record in records or []:
            if not record.get("market_date") or not record.get("commodity"):
                result["errors"] += 1
                result["error_rows"].append({
                    "commodity": record.get("commodity"),
                    "market_date": record.get("market_date"),
                    "reason": record.get("parse_error") or "missing commodity/date",
                    "raw_text": record.get("raw_text", ""),
                })
                continue

            key = {
                "commodity": record["commodity"],
                "market": record.get("market") or "National Aggregate",
                "market_date": record["market_date"],
            }
            existing = s["market_prices"].find_one(key)
            if existing is None:
                doc = dict(record)
                doc.update(key)
                doc["_id"] = _next_id(s.db)
                doc.setdefault("created_at", _now_iso())
                doc["synthetic"] = bool(record.get("synthetic", False))
                doc.setdefault("computed_at", None)
                s["market_prices"].insert_one(doc)
                result["inserted"] += 1
                continue

            incoming_synthetic = bool(record.get("synthetic", False))
            existing_synthetic = bool(existing.get("synthetic", False))
            if existing_synthetic and not incoming_synthetic:
                patch = dict(record)
                patch.pop("_id", None)
                patch["synthetic"] = False
                patch.setdefault("updated_at", _now_iso())
                s["market_prices"].update_one({"_id": existing["_id"]}, {"$set": patch})
                result["updated"] += 1
            else:
                result["skipped"] += 1

        log_entry = {
            "source": source or ingest_context or "upload",
            "inserted": result["inserted"],
            "updated": result["updated"],
            "skipped": result["skipped"],
            "errors": result["errors"],
            "error_rows": result["error_rows"][:50],
            "ingested_at": _now_iso(),
        }
        log_entry["_id"] = _next_id(s.db)
        s["market_ingest_log"].insert_one(log_entry)
        result["log_id"] = log_entry["_id"]
    return result


def list_market_prices(
    *,
    commodity=None,
    market=None,
    group=None,
    date_from=None,
    date_to=None,
    limit=500,
):
    query = {}
    if commodity:
        query["commodity"] = commodity
    if market:
        query["market"] = market
    if group:
        query["commodity_group"] = group
    if date_from or date_to:
        query["market_date"] = {}
        if date_from:
            query["market_date"]["$gte"] = date_from
        if date_to:
            query["market_date"]["$lte"] = date_to
    with session_scope() as s:
        docs = list(
            s["market_prices"]
            .find(query)
            .sort("market_date", -1)
            .limit(limit)
        )
        return [_market_row(d) for d in docs]


def price_series(commodity, market=None, limit=90):
    query = {"commodity": commodity}
    if market:
        query["market"] = market
    with session_scope() as s:
        docs = list(
            s["market_prices"]
            .find(query)
            .sort("market_date", 1)
            .limit(limit)
        )
    rows = []
    for d in docs:
        price = d.get("price_per_kg")
        msp = d.get("msp_per_kg")
        entry = _market_row(d)
        entry["trend"] = _trend_label(docs, d.get("market_date"))
        entry["vs_msp_pct"] = (
            round((price - msp) / msp * 100.0, 2)
            if price is not None and msp not in (None, 0) else None
        )
        rows.append(entry)
    return rows


def _trend_label(docs, market_date):
    if len(docs) < 2:
        return "insufficient"
    idx = next((i for i, d in enumerate(docs) if d.get("market_date") == market_date), None)
    if idx is None or idx == 0:
        return "stable"
    prior = docs[idx - 1].get("price_per_kg")
    current = docs[idx].get("price_per_kg")
    if prior is None or current is None:
        return "unknown"
    pct = (current - prior) / prior * 100.0
    if pct > 3:
        return "rising"
    if pct < -3:
        return "falling"
    return "stable"


def _market_row(doc):
    return {
        "id": doc.get("_id"),
        "commodity": doc.get("commodity"),
        "commodity_group": doc.get("commodity_group"),
        "market": doc.get("market"),
        "market_date": doc.get("market_date"),
        "price_per_kg": doc.get("price_per_kg"),
        "msp_per_kg": doc.get("msp_per_kg"),
        "arrival_metric_tonnes": doc.get("arrival_metric_tonnes"),
        "synthetic": bool(doc.get("synthetic", False)),
        "source": doc.get("source"),
        "report_generated_at": doc.get("report_generated_at"),
        "parse_error": doc.get("parse_error"),
    }


def distinct_commodities():
    with session_scope() as s:
        pipeline = [
            {"$group": {"_id": "$commodity_group", "items": {"$addToSet": "$commodity"}}},
            {"$sort": {"_id": 1}},
        ]
        groups = s["market_prices"].aggregate(pipeline)
        out = []
        for g in groups:
            out.append({
                "group": g["_id"],
                "commodities": sorted(g["items"]),
            })
    markets = _distinct("market")
    return {"groups": out, "markets": markets}


def _distinct(field):
    with session_scope() as s:
        return sorted(s["market_prices"].distinct(field))


def recent_ingest_log(limit=10):
    with session_scope() as s:
        docs = list(
            s["market_ingest_log"].find({}).sort("ingested_at", -1).limit(limit)
        )
        return [{
            "id": d.get("_id"),
            "source": d.get("source"),
            "inserted": d.get("inserted"),
            "updated": d.get("updated"),
            "skipped": d.get("skipped"),
            "errors": d.get("errors"),
            "ingested_at": d.get("ingested_at"),
        } for d in docs]


def ingest_status():
    with session_scope() as s:
        total = s["market_prices"].count_documents({})
        real = s["market_prices"].count_documents({"synthetic": False})
        synthetic = s["market_prices"].count_documents({"synthetic": True})
        latest = s["market_prices"].find_one({}, sort=[("market_date", -1)])
        return {
            "total_records": total,
            "real": real,
            "synthetic": synthetic,
            "latest_market_date": (latest or {}).get("market_date"),
            "unique_keys": None,
        }