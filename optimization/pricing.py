"""Dynamic pricing engine.

Combines live demand (recency-weighted sales velocity), current stock level,
a baseline margin and (optionally) competitor price bounds into a suggested
retail price that moves inside hard floor/ceiling limits so sellers never get
an absurd recommendation.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PriceSuggestion:
    base_price: float
    suggested_price: float
    change_pct: float
    demand_factor: float
    stock_factor: float
    competitor_adjustment: float
    reason: str


class DynamicPricingEngine:
    def __init__(
        self,
        margin: float = 0.20,
        max_discount: float = 0.30,
        max_premium: float = 0.60,
        stock_target: float = 100.0,
        demand_elasticity: float = 0.25,
    ) -> None:
        self.margin = margin
        self.max_discount = max_discount
        self.max_premium = max_premium
        self.stock_target = stock_target
        self.demand_elasticity = demand_elasticity

    def _clamp(self, price: float, base: float) -> float:
        floor = base * (1.0 - self.max_discount)
        ceiling = base * (1.0 + self.max_premium)
        return round(min(max(price, floor), ceiling), 4)

    def suggest_price(
        self,
        base_price: float,
        demand_score: float = 0.5,
        stock_level: float = 0.0,
        stock_target: Optional[float] = None,
        competitor_min: Optional[float] = None,
        competitor_max: Optional[float] = None,
        reference_price: Optional[float] = None,
    ) -> PriceSuggestion:
        base = max(float(base_price or 0.0), 0.0)
        if base <= 0:
            return PriceSuggestion(
                base_price=0.0, suggested_price=0.0, change_pct=0.0,
                demand_factor=0.0, stock_factor=0.0, competitor_adjustment=0.0,
                reason="no base price",
            )

        demand_score = max(0.0, min(1.0, float(demand_score)))
        target = self.stock_target if stock_target is None else float(stock_target)

        # demand_factor in [1 - d, 1 + d]: strong demand pushes price up.
        demand_factor = 1.0 + (demand_score - 0.5) * 2.0 * self.demand_elasticity

        # Low stock relative to target -> scarcity premium; excess stock -> discount.
        if target > 0:
            stock_ratio = max(0.0, float(stock_level)) / target
        else:
            stock_ratio = 1.0
        stock_factor = 1.0 + (0.5 - stock_ratio) * self.demand_elasticity

        suggested = base * demand_factor * stock_factor
        suggested = suggested * (1.0 + self.margin if reference_price is not None else 1.0)

        competitor_adjustment = 0.0
        reason = "baseline recommendation"
        if competitor_min is not None and competitor_max is not None:
            if suggested > competitor_max:
                competitor_adjustment = -0.05
                suggested *= 1.0 + competitor_adjustment
                reason = "matched to competitive ceiling"
            elif suggested < competitor_min:
                competitor_adjustment = 0.05
                suggested *= 1.0 + competitor_adjustment
                reason = "raised toward competitive floor"

        suggested = self._clamp(suggested, base)
        change_pct = round((suggested - base) / base, 4) if base else 0.0

        return PriceSuggestion(
            base_price=base,
            suggested_price=suggested,
            change_pct=change_pct,
            demand_factor=round(demand_factor, 4),
            stock_factor=round(stock_factor, 4),
            competitor_adjustment=round(competitor_adjustment, 4),
            reason=reason,
        )