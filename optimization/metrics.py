"""Sales analytics helpers shared by the pricing engine, forecasting and API.

These operate on sequences of MongoDB **documents** (dicts) rather than ORM
objects, so they work equally well for the REST API, the Streamlit dashboard
and the tests.
"""

import math
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


def _get_attr_or_dict(obj, key: str, default=None):
    """Get *key* from *obj* whether it's a dict or an object with attributes."""
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _get_qty(order) -> float:
    """Quantity from an order (dict or object with quantity_kg/quantity)."""
    return float(_get_attr_or_dict(order, "quantity_kg")
                 or _get_attr_or_dict(order, "quantity")
                 or 0.0)


def _parse_ts(ts) -> datetime:
    """Parse a timestamp from an order/review (dict or object with created_at).

    Always returns a timezone-aware UTC datetime so arithmetic is safe.
    """
    if ts is None:
        return datetime.now(timezone.utc)
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            return ts.replace(tzinfo=timezone.utc)
        return ts.astimezone(timezone.utc)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _sentiment_label(score: float) -> str:
    if score > 0.15:
        return "POSITIVE"
    if score < -0.15:
        return "NEGATIVE"
    return "NEUTRAL"


def demand_score(
    orders: List[dict],
    reference: datetime | None = None,
    half_life_days: float = 14.0,
    max_expected: int = 20,
) -> float:
    """Recency-weighted demand strength in [0, 1].

    0.5 means neutral/unknown demand; values above indicate healthy recent
    velocity, values below indicate sluggish markets.
    """
    now = reference or datetime.now(timezone.utc)
    count = len(orders)
    if count == 0:
        return 0.5

    total_delta = 0.0
    for order in orders:
        created = _parse_ts(_get_attr_or_dict(order, "created_at"))
        delta = (now - created).total_seconds() / 86400.0
        total_delta += max(delta, 0.0)

    avg_recency = math.exp(-(total_delta / count) / half_life_days)
    velocity = min(count / float(max_expected), 1.0)
    return round(0.5 * avg_recency + 0.5 * velocity, 4)


def sales_summary(orders: List[dict]) -> Dict[str, Any]:
    """Aggregate order-level metrics for a product/seller."""
    if not orders:
        return {
            "total_units": 0,
            "revenue": 0.0,
            "order_count": 0,
            "avg_order_value": 0.0,
            "first_sale": None,
            "last_sale": None,
        }

    units = sum(_get_qty(o) for o in orders)
    revenue = sum(float(_get_attr_or_dict(o, "total_price", 0.0)) for o in orders)
    dates = [_parse_ts(_get_attr_or_dict(o, "created_at")) for o in orders]
    return {
        "total_units": round(float(units), 3),
        "revenue": round(float(revenue), 2),
        "order_count": len(orders),
        "avg_order_value": round(float(revenue) / len(orders), 2),
        "first_sale": min(dates).isoformat() if dates else None,
        "last_sale": max(dates).isoformat() if dates else None,
    }


def revenue_by_day(orders: List[dict], days: int = 30) -> List[Dict[str, Any]]:
    """Roll up units and revenue per calendar day (UTC)."""
    buckets: Dict[date, Dict[str, float]] = {}
    now = datetime.now(timezone.utc)
    today = now.date()
    cutoff = today.toordinal() - days

    for order in orders:
        ts = _parse_ts(_get_attr_or_dict(order, "created_at"))
        d = ts.date()
        if d.toordinal() < cutoff:
            continue
        bucket = buckets.setdefault(d, {"units": 0.0, "revenue": 0.0})
        bucket["units"] += _get_qty(order)
        bucket["revenue"] += float(_get_attr_or_dict(order, "total_price", 0.0) or 0.0)

    rows = []
    for d in sorted(buckets):
        rows.append({
            "date": d.isoformat(),
            "units": round(buckets[d]["units"], 3),
            "revenue": round(buckets[d]["revenue"], 2),
        })
    return rows


def rating_summary(reviews: List[dict]) -> Dict[str, Any]:
    """Average rating plus count and sentiment distribution."""
    if not reviews:
        return {
            "count": 0,
            "avg_rating": 0.0,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
        }

    total_rating = 0.0
    rating_count = 0
    positive = neutral = negative = 0

    for r in reviews:
        rating = _get_attr_or_dict(r, "rating", 0)
        if rating:
            total_rating += rating
            rating_count += 1
        score = float(_get_attr_or_dict(r, "sentiment_score", 0.0) or 0.0)
        label = _sentiment_label(score)
        if label == "POSITIVE":
            positive += 1
        elif label == "NEUTRAL":
            neutral += 1
        else:
            negative += 1

    avg = total_rating / rating_count if rating_count else 0.0
    return {
        "count": len(reviews),
        "avg_rating": round(float(avg), 2),
        "positive": positive,
        "neutral": neutral,
        "negative": negative,
    }


def _sentiment_from_score(score: float) -> str:
    """Backward compat: legacy code called this."""
    return _sentiment_label(score)
