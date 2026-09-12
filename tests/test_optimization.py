"""Optimisation engine unit tests: recommendations, pricing, forecasting, metrics."""

from datetime import datetime, timedelta
from types import SimpleNamespace

from optimization.forecasting import forecast_demand, DemandForecaster
from optimization.metrics import demand_score, rating_summary, revenue_by_day, sales_summary
from optimization.pricing import DynamicPricingEngine
from optimization.recommendation import RecommendationEngine


def _txn(quantity: float, created_at: datetime, total_price: float = 0.0):
    return SimpleNamespace(quantity=quantity, created_at=created_at, total_price=total_price)


def test_recommendation_ranks_relevant_first():
    engine = RecommendationEngine().fit(
        [
            "fresh farm tomatoes and basil",
            "fresh ripe organic tomatoes",
            "spicy chili peppers",
        ],
        [1, 2, 3],
    )
    results = engine.recommend("fresh ripe tomatoes", top_k=3)
    assert results
    assert results[0][0] == 2
    assert results[0][1] > results[1][1]


def test_recommendation_similar_excludes_self():
    engine = RecommendationEngine().fit(
        [
            "fresh farm tomatoes and basil",
            "fresh ripe organic tomatoes",
            "spicy chili peppers",
        ],
        [10, 20, 30],
    )
    similar = engine.similar(0)
    ids = [id_ for id_, _ in similar]
    assert 10 not in ids
    assert 20 in ids


def test_recommendation_empty_corpus_is_safe():
    engine = RecommendationEngine().fit([], [])
    assert engine.recommend("anything") == []


def test_pricing_premiums_low_stock_high_demand():
    engine = DynamicPricingEngine()
    suggestion = engine.suggest_price(
        base_price=10.0, demand_score=0.95, stock_level=5, stock_target=100
    )
    assert suggestion.suggested_price > 10.0
    assert suggestion.change_pct > 0


def test_pricing_discounts_excess_stock():
    engine = DynamicPricingEngine()
    suggestion = engine.suggest_price(
        base_price=10.0, demand_score=0.05, stock_level=300, stock_target=100
    )
    assert suggestion.suggested_price < 10.0


def test_pricing_respects_floor_and_ceiling():
    engine = DynamicPricingEngine()
    suggestion = engine.suggest_price(
        base_price=10.0, demand_score=1.0, stock_level=0, stock_target=100
    )
    assert 10.0 * (1 - engine.max_discount) <= suggestion.suggested_price <= 10.0 * (1 + engine.max_premium)


def test_pricing_zero_base_is_neutral():
    suggestion = DynamicPricingEngine().suggest_price(base_price=0.0)
    assert suggestion.suggested_price == 0.0


def test_forecast_returns_horizon_values():
    history = [10, 12, 14, 19, 22, 24, 26, 28, 31, 33]
    result = forecast_demand(history, horizon=5)
    assert len(result["values"]) == 5
    assert len(result["dates"]) == 5
    assert all(value >= 0 for value in result["values"])
    assert result["history"] == history


def test_forecast_single_point_uses_level():
    result = forecast_demand([42.0], horizon=3)
    assert result["values"] == [42.0, 42.0, 42.0]


def test_forecast_empty_history():
    result = forecast_demand([], horizon=3)
    assert result["values"] == [0.0, 0.0, 0.0]


def test_forecast_from_transactions():
    now = datetime.utcnow()
    transactions = [
        _txn(5, now - timedelta(days=4)),
        _txn(8, now - timedelta(days=3)),
        _txn(6, now - timedelta(days=1)),
    ]
    result = DemandForecaster.from_transactions(transactions, horizon=7)
    assert result["history"] == [5.0, 8.0, 0.0, 6.0]
    assert len(result["history_dates"]) == 4
    assert len(result["dates"]) == 7
    assert len(result["values"]) == 7


def test_demand_score_warm_series_beats_cold():
    now = datetime.utcnow()
    warm = [_txn(5, now - timedelta(hours=2)) for _ in range(8)]
    cold = [_txn(5, now - timedelta(days=25)) for _ in range(8)]
    assert demand_score(warm) > demand_score(cold)


def test_sales_summary_aggregates():
    now = datetime.utcnow()
    transactions = [_txn(2.0, now), _txn(3.0, now - timedelta(days=1))]
    transactions[0].total_price = 10.0
    transactions[1].total_price = 15.0
    summary = sales_summary(transactions)
    assert summary["total_units"] == 5.0
    assert summary["revenue"] == 25.0
    assert summary["order_count"] == 2


def test_revenue_by_day_buckets():
    now = datetime.utcnow()
    transactions = [_txn(1.0, now), _txn(2.0, now - timedelta(days=1))]
    transactions[0].total_price = 3.0
    transactions[1].total_price = 6.0
    rows = revenue_by_day(transactions)
    assert len(rows) == 2
    assert sum(row["revenue"] for row in rows) == 9.0


def test_rating_summary_empty():
    assert rating_summary([])["count"] == 0


def test_pricing_engine_competitive_adjustment():
    engine = DynamicPricingEngine()
    high = engine.suggest_price(
        base_price=50.0, demand_score=0.9, stock_level=10,
        competitor_min=5.0, competitor_max=8.0,
    )
    assert high.suggested_price <= 50.0 * (1 + engine.max_premium)