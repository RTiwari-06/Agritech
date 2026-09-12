"""Sales analytics helpers shared by the pricing engine, forecasting and API.

These operate on sequences of ORM objects (``Order`` / ``Review``) so the
same code serves the REST API, the Streamlit dashboard and the tests.
"""

import math
from datetime import date, datetime
from typing import Any, Dict, List

from database.models import Order, Review


def demand_score(
    orders: List[Order],
    reference: datetime | None = None,
    half_life_days: float = 14.0,
    max_expected: int = 20,
) -> float:
    """Recency-weighted demand strength in [0, 1].

    0.5 means neutral/unknown demand; values above indicate healthy recent
    velocity, values below indicate sluggish markets.
    """
    now = reference or datetime.utcnow()
    count = len(orders)
    if count == 0:
        return 0.5

    days_ago = 0.0
    for order in orders:
        created = getattr(order, "created_at", None)
        if created is not None:
            delta = (now - created).total_seconds() / 86400.0
            days_ago += max(delta, 0.0)

    avg_recency = math.exp(-(days_ago / count) / half_life_days)
    velocity = min(count / float(max_expected), 1.0)
    return round(0.5 * avg_recency + 0.5 * velocity, 4)


def _get_quantity(order) -> float:
    """Get quantity from order (supports both quantity_kg and quantity)."""
    return getattr(order, "quantity_kg", getattr(order, "quantity", 0.0))


def sales_summary(orders: List[Order]) -> Dict[str, Any]:
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

    units = sum(_get_quantity(o) for o in orders if _get_quantity(o))
    revenue = sum(o.total_price for o in orders if o.total_price)
    dates = [o.created_at for o in orders if o.created_at]
    return {
        "total_units": round(float(units), 3),
        "revenue": round(float(revenue), 2),
        "order_count": len(orders),
        "avg_order_value": round(float(revenue) / len(orders), 2),
        "first_sale": min(dates, default=None),
        "last_sale": max(dates, default=None),
    }


def revenue_by_day(orders: List[Order], days: int = 30) -> List[Dict[str, Any]]:
    """Roll up units and revenue per calendar day (UTC)."""
    buckets: Dict[date, Dict[str, float]] = {}
    today = datetime.utcnow().date()
    for order in orders:
        created = getattr(order, "created_at", None)
        if created is None:
            created = today
        if isinstance(created, datetime):
            created = created.date()
        if (today - created).days > days:
            continue
        bucket = buckets.setdefault(created, {"units": 0.0, "revenue": 0.0})
        bucket["units"] += float(_get_quantity(order) or 0.0)
        bucket["revenue"] += float(order.total_price or 0.0)

    rows = []
    for created in sorted(buckets):
        rows.append(
            {
                "date": created.isoformat(),
                "units": round(buckets[created]["units"], 3),
                "revenue": round(buckets[created]["revenue"], 2),
            }
        )
    return rows


def rating_summary(reviews: List[Review]) -> Dict[str, Any]:
    """Average rating plus count and sentiment distribution."""
    if not reviews:
        return {
            "count": 0,
            "avg_rating": 0.0,
            "positive": 0,
            "neutral": 0,
            "negative": 0,
        }
    avg = sum(r.rating for r in reviews if r.rating) / len(reviews)
    # Compute sentiment label from score
    labels = []
    for r in reviews:
        score = r.sentiment_score or 0.0
        if score > 0.15:
            labels.append("POSITIVE")
        elif score < -0.15:
            labels.append("NEGATIVE")
        else:
            labels.append("NEUTRAL")
    return {
        "count": len(reviews),
        "avg_rating": round(float(avg), 2),
        "positive": labels.count("POSITIVE"),
        "neutral": labels.count("NEUTRAL"),
        "negative": labels.count("NEGATIVE"),
    }