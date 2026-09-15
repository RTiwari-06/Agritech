"""Demand forecasting.

Combines a linear trend (scikit-learn) with a trailing moving average so short
horizon forecasts stay stable, and exposes point estimates per future day.
"""

from statistics import mean
from typing import Dict, List

import numpy as np
from sklearn.linear_model import LinearRegression


def _dates_forward(horizon: int, start_ordinal: int) -> List[str]:
    from datetime import date, timedelta

    base = date.fromordinal(start_ordinal)
    return [(base + timedelta(days=i)).isoformat() for i in range(1, horizon + 1)]


def forecast_demand(history: List[float], horizon: int = 7) -> Dict:
    """Forecast the next ``horizon`` demand values from a trailing series.

    Parameters
    ----------
    history : list[float]
        Observed per-day quantities (chronological).
    horizon : int
        Number of future days to predict (>= 1).

    Returns
    -------
    dict with keys: ``history``, ``dates``, ``values``, ``trend_slope``.
    """
    horizon = max(int(horizon), 1)
    clean = [float(v) for v in history if v is not None]
    if not clean:
        return {
            "history": [],
            "dates": _dates_forward(horizon, 1),
            "values": [0.0] * horizon,
            "trend_slope": 0.0,
        }

    length = len(clean)
    x = np.arange(length, dtype=float).reshape(-1, 1)
    y = np.array(clean, dtype=float)

    trend_slope = 0.0
    predictions = []
    if length >= 2:
        model = LinearRegression()
        model.fit(x, y)
        future_x = np.arange(length, length + horizon, dtype=float).reshape(-1, 1)
        predictions = model.predict(future_x).tolist()
        trend_slope = float(model.coef_[0])
    else:
        predictions = [clean[-1]] * horizon

    # Damp linear extrapolation with a trailing moving average.
    window = min(7, length)
    trailing_eta = mean(clean[-window:]) if window else clean[-1]
    blended = [0.6 * value + 0.4 * trailing_eta for value in predictions]
    blended = [max(0.0, round(value, 3)) for value in blended]

    return {
        "history": clean,
        "dates": _dates_forward(horizon, length + 1),
        "values": blended,
        "trend_slope": round(trend_slope, 4),
    }


class DemandForecaster:
    """Convenience wrapper around :func:`forecast_demand`."""

    def forecast(self, history: List[float], horizon: int = 7) -> Dict:
        return forecast_demand(history, horizon)

    @staticmethod
    def from_transactions(
        transactions, horizon: int = 7, bucket: str = "day"
    ) -> Dict:
        """Build a daily demand series from Order ORM rows."""
        from datetime import date, datetime, timedelta

        by_day: Dict[date, float] = {}
        for txn in transactions:
            created = getattr(txn, "created_at", None)
            if created is None:
                created = datetime.utcnow()  # naive, matches DB
            if isinstance(created, datetime):
                created = created.date()
            # Order uses quantity_kg
            qty = getattr(txn, "quantity_kg", None) or getattr(txn, "quantity", 0.0)
            by_day[created] = by_day.get(created, 0.0) + float(qty)

        if not by_day:
            return {
                "history": [],
                "history_dates": [],
                "dates": [],
                "values": [0.0] * horizon,
                "trend_slope": 0.0,
            }

        start, end = min(by_day), max(by_day)
        series, cursor = [], start
        while cursor <= end:
            series.append(by_day.get(cursor, 0.0))
            cursor += timedelta(days=1)

        result = forecast_demand(series, horizon)
        result["history_dates"] = [
            (start + timedelta(days=i)).isoformat() for i in range(len(series))
        ]
        result["dates"] = [
            (end + timedelta(days=i + 1)).isoformat() for i in range(horizon)
        ]
        result["history"] = series
        return result