"""Optimisation engines: recommendations, dynamic pricing, demand forecasting."""

from optimization.engine import (
    DemandForecaster,
    DynamicPricingEngine,
    RecommendationEngine,
    create_demand_forecaster,
    create_pricing_engine,
    create_recommendation_engine,
)
from optimization.forecasting import DemandForecaster as LegacyDemandForecaster, forecast_demand
from optimization.metrics import demand_score, revenue_by_day, sales_summary
from optimization.pricing import DynamicPricingEngine as LegacyDynamicPricingEngine
from optimization.recommendation import RecommendationEngine as LegacyRecommendationEngine

__all__ = [
    "DemandForecaster",
    "DynamicPricingEngine",
    "RecommendationEngine",
    "create_demand_forecaster",
    "create_pricing_engine",
    "create_recommendation_engine",
    "forecast_demand",
    "demand_score",
    "revenue_by_day",
    "sales_summary",
    # Legacy aliases
    "LegacyDemandForecaster",
    "LegacyDynamicPricingEngine",
    "LegacyRecommendationEngine",
]