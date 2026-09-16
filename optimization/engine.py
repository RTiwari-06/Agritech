"""Optimization suite: Recommendation, Dynamic Pricing, and Demand Forecasting engines."""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from config import Config

logger = logging.getLogger(__name__)

try:
    from sklearn.ensemble import RandomForestRegressor
    from sklearn.linear_model import LinearRegression
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    SKLEARN_AVAILABLE = True
except ImportError:
    SKLEARN_AVAILABLE = False
    logger.warning("scikit-learn not available. Using fallback implementations.")


@dataclass
class RecommendationResult:
    product_id: int
    score: float
    reason: str


@dataclass
class PriceCalculation:
    product_id: int
    base_price: float
    optimal_price: float
    demand_factor: float
    stock_factor: float
    sentiment_factor: float
    change_pct: float
    capped: bool
    reason: str = "dynamic pricing"
    live_factor: float = 0.0


@dataclass
class DemandForecast:
    product_id: int
    forecast_days: int
    predictions: List[Dict[str, Any]]
    trend_slope: float
    model_used: str


class RecommendationEngine:
    """Hybrid recommendation engine combining content-based and sentiment-weighted filtering."""

    def __init__(self, top_n: int = 5) -> None:
        self.top_n = top_n
        self._vectorizer = None
        self._product_matrix = None
        self._product_ids: List[int] = []
        self._product_texts: List[str] = []
        self._fitted = False

    def fit(self, products: List[Dict[str, Any]]) -> "RecommendationEngine":
        """Fit the engine on product catalog."""
        if not products:
            self._fitted = False
            return self

        self._product_ids = [p["id"] for p in products]
        self._product_texts = [
            " ".join(filter(None, [
                str(p.get("name", "")),
                str(p.get("category", "")),
                str(p.get("description", "")),
                str(p.get("keywords", ""))
            ]))
            for p in products
        ]

        if SKLEARN_AVAILABLE and len(self._product_texts) > 1:
            try:
                self._vectorizer = TfidfVectorizer(
                    stop_words="english", ngram_range=(1, 2), max_features=3000
                )
                self._product_matrix = self._vectorizer.fit_transform(self._product_texts)
                self._fitted = True
            except Exception as e:
                logger.warning(f"TF-IDF fitting failed: {e}")
                self._fitted = False
        else:
            self._fitted = False

        return self

    def get_recommendations(
        self,
        user_id: int,
        top_n: int = 5,
        user_purchase_history: Optional[List[Dict]] = None,
        product_sentiments: Optional[Dict[int, float]] = None
    ) -> List[Dict[str, Any]]:
        """Get hybrid recommendations for a user.

        Combines:
        1. Content-based: TF-IDF similarity to user's past purchases
        2. Sentiment-weighted: Boost products with high positive sentiment
        """
        if not self._fitted or not self._product_ids:
            return []

        # Build user profile from purchase history
        user_profile_text = ""
        if user_purchase_history:
            purchased_ids = [p["product_id"] for p in user_purchase_history]
            purchased_indices = [
                i for i, pid in enumerate(self._product_ids) if pid in purchased_ids
            ]
            if purchased_indices:
                user_profile_text = " ".join(
                    self._product_texts[i] for i in purchased_indices
                )

        # If no purchase history, use a generic query
        if not user_profile_text:
            user_profile_text = "fresh organic vegetables fruits grains quality"

        try:
            query_vec = self._vectorizer.transform([user_profile_text])
            similarities = cosine_similarity(query_vec, self._product_matrix)[0]
        except Exception:
            return []

        # Apply sentiment weighting
        if product_sentiments:
            for i, pid in enumerate(self._product_ids):
                sentiment = product_sentiments.get(pid, 0.0)
                # Boost by up to 20% based on sentiment (-1 to 1 mapped to 0.8 to 1.2)
                sentiment_boost = 1.0 + (sentiment * 0.2)
                similarities[i] *= sentiment_boost

        # Get top N
        top_indices = np.argsort(similarities)[::-1][:top_n]

        results = []
        for idx in top_indices:
            if similarities[idx] > 0:
                results.append({
                    "product_id": self._product_ids[idx],
                    "score": round(float(similarities[idx]), 4),
                    "reason": "content_match" if user_purchase_history else "popular"
                })

        return results

    def get_similar_products(self, product_id: int, top_n: int = 5) -> List[Dict[str, Any]]:
        """Get products similar to a given product."""
        if not self._fitted or product_id not in self._product_ids:
            return []

        idx = self._product_ids.index(product_id)
        try:
            similarities = cosine_similarity(
                self._product_matrix[idx], self._product_matrix
            )[0]
        except Exception:
            return []

        # Exclude self
        similarities[idx] = -1
        top_indices = np.argsort(similarities)[::-1][:top_n]

        results = []
        for i in top_indices:
            if similarities[i] > 0:
                results.append({
                    "product_id": self._product_ids[i],
                    "score": round(float(similarities[i]), 4),
                    "reason": "similar_content"
                })

        return results


class DynamicPricingEngine:
    """Dynamic pricing engine based on demand, stock, and sentiment."""

    def __init__(
        self,
        min_price_ratio: float = 0.8,
        max_price_ratio: float = 1.2,
        stock_high_threshold: float = 500.0,
        sentiment_boost_max: float = 0.05
    ) -> None:
        self.min_price_ratio = min_price_ratio
        self.max_price_ratio = max_price_ratio
        self.stock_high_threshold = stock_high_threshold
        self.sentiment_boost_max = sentiment_boost_max

    def calculate_optimal_price(
        self,
        product_id: int,
        base_price: float,
        demand_factor: float = 0.5,
        stock_kg: float = 0.0,
        sentiment_score: float = 0.0,
        live_engagement: Optional[float] = None
    ) -> PriceCalculation:
        """Calculate optimal price using the formula:
        Optimal Price = Base Price * (1 + Demand Factor - Stock Factor + Sentiment Factor)
        """
        base = max(float(base_price), 0.0)
        if base <= 0:
            return PriceCalculation(
                product_id=product_id,
                base_price=0.0,
                optimal_price=0.0,
                demand_factor=0.0,
                stock_factor=0.0,
                sentiment_factor=0.0,
                change_pct=0.0,
                capped=False
            )

        # Demand Factor: 0.0 to 1.0, higher demand -> higher price
        # Maps to [-0.1, +0.1] range (10% swing)
        demand_adj = (demand_factor - 0.5) * 0.2

        # Stock Factor: Higher discount if stock > threshold
        if stock_kg > self.stock_high_threshold:
            # Excess stock: discount up to 10%
            excess_ratio = min(stock_kg / self.stock_high_threshold, 3.0)
            stock_adj = min(0.1, 0.05 * (excess_ratio - 1.0))
        elif stock_kg < 50.0:
            # Low stock: premium up to 10%
            stock_adj = -min(0.1, 0.05 * (50.0 / max(stock_kg, 1.0)))
        else:
            stock_adj = 0.0

        # Sentiment Factor: Positive sentiment boosts pricing capability up to 5%
        # sentiment_score is -1 to 1, map to 0 to 0.05
        sentiment_adj = max(0.0, sentiment_score) * self.sentiment_boost_max

        # Live-stream engagement factor: chat-driven demand signal, +/-5% swing.
        # None means "no live signal" and stays fully neutral.
        live_adj = 0.0
        if live_engagement is not None:
            live_adj = (max(0.0, min(1.0, float(live_engagement))) - 0.5) * 0.1

        # Calculate optimal price
        multiplier = 1.0 + demand_adj - stock_adj + sentiment_adj + live_adj
        optimal_price = base * multiplier

        # Cap within bounds
        min_price = base * self.min_price_ratio
        max_price = base * self.max_price_ratio
        capped = False

        if optimal_price < min_price:
            optimal_price = min_price
            capped = True
        elif optimal_price > max_price:
            optimal_price = max_price
            capped = True

        change_pct = round((optimal_price - base) / base, 4) if base else 0.0

        return PriceCalculation(
            product_id=product_id,
            base_price=base,
            optimal_price=round(optimal_price, 2),
            demand_factor=round(demand_adj, 4),
            stock_factor=round(stock_adj, 4),
            sentiment_factor=round(sentiment_adj, 4),
            change_pct=change_pct,
            capped=capped,
            live_factor=round(live_adj, 4),
        )


class DemandForecaster:
    """Demand forecasting using scikit-learn models."""

    def __init__(self, model_type: str = "random_forest") -> None:
        self.model_type = model_type
        self._model = None
        self._trained = False

    def _prepare_features(self, history: List[float], horizon: int) -> Tuple[np.ndarray, np.ndarray]:
        """Prepare features for training."""
        # Create time-based features
        n = len(history)
        X = np.arange(n).reshape(-1, 1)
        y = np.array(history, dtype=float)

        # Add cyclical features for seasonality (weekly)
        X_extended = np.column_stack([
            X,
            np.sin(2 * np.pi * X / 7),
            np.cos(2 * np.pi * X / 7),
            np.sin(2 * np.pi * X / 30),
            np.cos(2 * np.pi * X / 30)
        ])

        # Future features
        future_X = np.arange(n, n + horizon).reshape(-1, 1)
        future_X_extended = np.column_stack([
            future_X,
            np.sin(2 * np.pi * future_X / 7),
            np.cos(2 * np.pi * future_X / 7),
            np.sin(2 * np.pi * future_X / 30),
            np.cos(2 * np.pi * future_X / 30)
        ])

        return X_extended, y, future_X_extended

    def predict_demand(
        self,
        product_id: int,
        historical_data: List[Dict[str, Any]],
        forecast_days: int = 7
    ) -> DemandForecast:
        """Predict daily demand for the next N days.

        historical_data: List of dicts with keys: date (str), quantity (float), sentiment (float, optional)
        """
        if not historical_data:
            return DemandForecast(
                product_id=product_id,
                forecast_days=forecast_days,
                predictions=[{"date": (date.today() + timedelta(days=i)).isoformat(), "predicted_quantity": 0.0} for i in range(1, forecast_days + 1)],
                trend_slope=0.0,
                model_used="none"
            )

        # Extract quantities and dates
        quantities = [float(d.get("quantity", 0)) for d in historical_data]
        dates = [d.get("date") for d in historical_data]

        # Try ML model if available
        if SKLEARN_AVAILABLE and len(quantities) >= 5:
            try:
                X, y, future_X = self._prepare_features(quantities, forecast_days)

                if self.model_type == "random_forest":
                    model = RandomForestRegressor(n_estimators=50, random_state=42, max_depth=5)
                else:
                    model = LinearRegression()

                model.fit(X, y)
                predictions = model.predict(future_X)
                predictions = np.maximum(predictions, 0)  # No negative demand

                # Get trend slope from linear component
                if hasattr(model, 'coef_'):
                    trend_slope = float(model.coef_[0])
                else:
                    # For RF, approximate with linear fit on predictions
                    trend_slope = float(np.polyfit(range(len(predictions)), predictions, 1)[0])

                model_used = self.model_type
            except Exception as e:
                logger.warning(f"ML forecasting failed: {e}, falling back to linear")
                predictions, trend_slope, model_used = self._linear_fallback(quantities, forecast_days)
        else:
            predictions, trend_slope, model_used = self._linear_fallback(quantities, forecast_days)

        # Format predictions
        last_date = date.today()
        if dates:
            try:
                last_date = datetime.fromisoformat(dates[-1]).date()
            except Exception:
                pass

        forecast_predictions = []
        for i, pred in enumerate(predictions):
            forecast_predictions.append({
                "date": (last_date + timedelta(days=i + 1)).isoformat(),
                "predicted_quantity": round(float(pred), 2),
                "day_ahead": i + 1
            })

        return DemandForecast(
            product_id=product_id,
            forecast_days=forecast_days,
            predictions=forecast_predictions,
            trend_slope=round(trend_slope, 4),
            model_used=model_used
        )

    def _linear_fallback(
        self, quantities: List[float], horizon: int
    ) -> Tuple[np.ndarray, float, str]:
        """Simple linear regression fallback."""
        n = len(quantities)
        x = np.arange(n, dtype=float)
        y = np.array(quantities, dtype=float)

        if n >= 2:
            slope, intercept = np.polyfit(x, y, 1)
            future_x = np.arange(n, n + horizon, dtype=float)
            predictions = slope * future_x + intercept
            predictions = np.maximum(predictions, 0)
        else:
            predictions = np.full(horizon, quantities[-1] if quantities else 0.0)
            slope = 0.0

        return predictions, float(slope), "linear_fallback"

    def get_seasonal_factors(self, months: int = 12) -> Dict[int, float]:
        """Get seasonal adjustment factors for each month (1-12)."""
        # Indian agricultural seasonality
        # Peak harvest: Oct-Dec (Kharif), Apr-Jun (Rabi)
        factors = {
            1: 1.1, 2: 1.05, 3: 1.0, 4: 1.15, 5: 1.2, 6: 1.15,
            7: 0.9, 8: 0.85, 9: 0.9, 10: 1.1, 11: 1.2, 12: 1.15
        }
        return {m: factors.get(m, 1.0) for m in range(1, months + 1)}


def create_recommendation_engine(top_n: int = 5) -> RecommendationEngine:
    """Factory function to create a recommendation engine."""
    return RecommendationEngine(top_n=top_n)


def create_pricing_engine() -> DynamicPricingEngine:
    """Factory function to create a dynamic pricing engine."""
    return DynamicPricingEngine()


def create_demand_forecaster(model_type: str = "random_forest") -> DemandForecaster:
    """Factory function to create a demand forecaster."""
    return DemandForecaster(model_type=model_type)


if __name__ == "__main__":
    print("=" * 60)
    print("Optimization Engines Self-Test")
    print("=" * 60)

    # Test RecommendationEngine
    print("\n--- RecommendationEngine ---")
    rec_engine = RecommendationEngine()
    products = [
        {"id": 1, "name": "Organic Tomatoes", "category": "Vegetable", "description": "Fresh organic tomatoes", "keywords": "organic fresh"},
        {"id": 2, "name": "Basmati Rice", "category": "Grain", "description": "Premium basmati rice", "keywords": "rice grain premium"},
        {"id": 3, "name": "Alphonso Mangoes", "category": "Fruit", "description": "Sweet alphonso mangoes", "keywords": "mango fruit sweet"},
        {"id": 4, "name": "Organic Spinach", "category": "Vegetable", "description": "Fresh organic spinach", "keywords": "organic fresh leafy"},
        {"id": 5, "name": "Toor Dal", "category": "Grain", "description": "Organic pigeon pea", "keywords": "dal organic protein"},
    ]
    rec_engine.fit(products)

    # With purchase history
    history = [{"product_id": 1}, {"product_id": 4}]
    sentiments = {1: 0.8, 2: 0.5, 3: 0.9, 4: 0.7, 5: 0.3}
    recs = rec_engine.get_recommendations(1, top_n=3, user_purchase_history=history, product_sentiments=sentiments)
    print(f"Recommendations for user with veg history: {recs}")

    # Test DynamicPricingEngine
    print("\n--- DynamicPricingEngine ---")
    pricing = DynamicPricingEngine()
    calc = pricing.calculate_optimal_price(
        product_id=1,
        base_price=100.0,
        demand_factor=0.8,
        stock_kg=600.0,
        sentiment_score=0.6
    )
    print(f"Price calc: {calc}")

    calc2 = pricing.calculate_optimal_price(
        product_id=2,
        base_price=200.0,
        demand_factor=0.3,
        stock_kg=50.0,
        sentiment_score=-0.2
    )
    print(f"Price calc (low demand, low stock): {calc2}")

    # Test DemandForecaster
    print("\n--- DemandForecaster ---")
    forecaster = DemandForecaster()
    hist = [
        {"date": "2024-01-01", "quantity": 10},
        {"date": "2024-01-02", "quantity": 12},
        {"date": "2024-01-03", "quantity": 11},
        {"date": "2024-01-04", "quantity": 15},
        {"date": "2024-01-05", "quantity": 13},
        {"date": "2024-01-06", "quantity": 14},
        {"date": "2024-01-07", "quantity": 16},
        {"date": "2024-01-08", "quantity": 15},
        {"date": "2024-01-09", "quantity": 18},
        {"date": "2024-01-10", "quantity": 17},
    ]
    forecast = forecaster.predict_demand(1, hist, forecast_days=7)
    print(f"Forecast: {forecast.predictions}")
    print(f"Trend slope: {forecast.trend_slope}, Model: {forecast.model_used}")

    print("\n" + "=" * 60)
    print("All self-tests completed!")
    print("=" * 60)