from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from lseg_quant.mdu.decision import LLMDecision, FinalTrade

logger = logging.getLogger(__name__)


@dataclass
class RiskOverrideConfig:
    confidence_floor: float = 0.75
    drawdown_hard: float = -0.25
    drawdown_soft: float = -0.15
    risk_off_cap: float = 0.30
    max_name_weight: float = 0.10
    pe_vs_history_ceiling: float = 1.75
    min_adv_multiple: float = 20.0
    max_dispersion: float = 0.40
    # Froth-score tiered caps (regime_froth_score.md): extreme froth caps
    # allocations regardless of regime; deep capitulation loosens the cap.
    froth_extreme_threshold: float = 80.0
    froth_extreme_cap: float = 0.15
    froth_calm_threshold: float = 20.0
    froth_calm_cap: float = 0.60


GATE_LABELS: dict[str, str] = {
    "data_quality": "Gate 1 — Data Quality",
    "confidence": "Gate 2 — Confidence Floor",
    "news_shock": "Gate 3 — News Shock",
    "drawdown": "Gate 4 — Drawdown",
    "risk_off": "Gate 5 — Risk-Off Regime",
    "valuation": "Gate 6 — Valuation",
    "dispersion": "Gate 7 — Consensus Dispersion",
    "name_cap": "Gate 8 — Per-Name Cap",
    "liquidity": "Gate 9 — Liquidity/ADV",
}


class RiskOverrideEngine:
    """9-gate risk override layer.

    Each gate can tighten (never loosen) the LLM/analyst decision.
    Gates are evaluated in order; later gates cannot undo earlier ones.

    This is the SINGLE risk layer for the system. See `validate_decision`
    in decision.py for the legacy layer (to be removed after migration).
    """

    def __init__(self, config: RiskOverrideConfig | None = None) -> None:
        self.config = config or RiskOverrideConfig()
        self._fired: list[str] = []

    @property
    def fired_gates(self) -> list[str]:
        return list(self._fired)

    def apply(
        self,
        decision: LLMDecision,
        features: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
        current_position_value: float = 0.0,
        portfolio_value: float = 100_000.0,
    ) -> FinalTrade:
        """Apply all 9 risk gates sequentially.

        Args:
            decision: The LLM/analyst decision to override.
            features: Feature vector from features.py (or dict with same keys).
            context: Enriched context dict with valuation, drawdown, etc.
            current_position_value: Current USD value of position in this name.
            portfolio_value: Total portfolio USD value.

        Returns:
            FinalTrade with risk_overrides populated.
        """
        self._fired = []
        features = features or {}
        context = context or {}

        action = decision.action
        confidence = decision.confidence
        allocation = decision.allocation
        reasoning = list(decision.reasoning)
        risk_flags = list(decision.risk_flags)

        action, confidence, allocation, reasoning, risk_flags = self._gate_1_data_quality(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_2_confidence(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_3_news_shock(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_4_drawdown(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_5_risk_off(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_6_valuation(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_7_dispersion(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_8_name_cap(
            action, confidence, allocation, reasoning, risk_flags, current_position_value, portfolio_value,
        )
        action, confidence, allocation, reasoning, risk_flags = self._gate_9_liquidity(
            action, confidence, allocation, reasoning, risk_flags, features, context,
        )

        if action not in ("BUY", "SELL", "HOLD"):
            self._fired.append("invalid_action")
            action = "HOLD"
            allocation = 0.0

        return FinalTrade(
            action=action,
            confidence=max(0.0, min(1.0, confidence)),
            allocation=max(0.0, min(1.0, allocation)),
            reasoning=reasoning[:5],
            risk_flags=risk_flags,
            risk_overrides=self._fired,
        )

    # ------------------------------------------------------------------
    # Individual gates
    # ------------------------------------------------------------------

    def _log_override(self, gate: str, msg: str) -> None:
        self._fired.append(f"{gate}: {msg}")
        logger.info("[%s] %s", GATE_LABELS.get(gate, gate), msg)

    def _gate_1_data_quality(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        dq = context.get("data_quality", {})
        if not dq:
            return action, confidence, allocation, reasoning, risk_flags

        missing = [k for k, v in dq.items() if v in ("missing", "unavailable")]
        if len(missing) >= 4:
            self._log_override("data_quality", f"{len(missing)} modules unavailable: {', '.join(missing)}")
            return ("HOLD", 0.0, 0.0,
                    [f"HOLD \u2014 {len(missing)} data modules unavailable"] + reasoning[:4],
                    risk_flags)

        price_ok = dq.get("price_data", "missing")
        if price_ok in ("missing", "insufficient"):
            self._log_override("data_quality", "price data missing")
            return ("HOLD", 0.0, 0.0,
                    ["HOLD \u2014 price data unavailable"] + reasoning[:4],
                    risk_flags)

        return action, confidence, allocation, reasoning, risk_flags

    def _gate_2_confidence(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        if action == "HOLD":
            return action, confidence, allocation, reasoning, risk_flags
        if confidence < self.config.confidence_floor:
            self._log_override("confidence", f"confidence {confidence:.2f} < {self.config.confidence_floor}")
            return ("HOLD", confidence, 0.0,
                    [f"HOLD \u2014 confidence {confidence:.2f} below {self.config.confidence_floor} floor"] + reasoning[:4],
                    risk_flags)
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_3_news_shock(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        shock = features.get("news_shock_flag") or context.get("news_shock", False)
        if shock and action in ("BUY",):
            self._log_override("news_shock", "news shock detected")
            return ("HOLD", confidence, 0.0,
                    ["HOLD \u2014 news shock detected"] + reasoning[:4],
                    risk_flags + ["news_shock"])
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_4_drawdown(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        max_dd_6m = features.get("max_drawdown_6m", 0.0)
        if isinstance(max_dd_6m, (int, float)) and max_dd_6m < self.config.drawdown_hard:
            self._log_override("drawdown", f"max drawdown {max_dd_6m:.2%} < {self.config.drawdown_hard}")
            new_action = "HOLD"
            if action == "SELL":
                new_action = "SELL"
            return (new_action, confidence, 0.0,
                    [f"{new_action} \u2014 drawdown {max_dd_6m:.2%} exceeds hard threshold ({self.config.drawdown_hard})"] + reasoning[:4],
                    risk_flags + [f"drawdown_{max_dd_6m:.0%}"])
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_5_risk_off(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        """Risk-off regime + tiered froth-score caps.

        Priority (tightest wins):
          1. froth >= extreme_threshold  -> froth_extreme_cap (any regime)
          2. risk-off regime             -> risk_off_cap, unless froth <=
             calm_threshold, which lifts it to froth_calm_cap
        """
        regime = features.get("macro_regime", "neutral")
        froth = features.get("froth_composite")

        if froth is not None and froth >= self.config.froth_extreme_threshold:
            cap = self.config.froth_extreme_cap
            if allocation > cap:
                self._log_override(
                    "risk_off",
                    f"allocation capped {allocation:.2%} \u2192 {cap:.0%} "
                    f"(froth score {froth:.0f} >= {self.config.froth_extreme_threshold:.0f})")
                return (action, confidence, cap,
                        [f"Allocation capped to {cap:.0%} "
                         f"(froth {froth:.0f}, extreme froth)"] + reasoning[:4],
                        risk_flags + ["froth_extreme_cap"])
            return action, confidence, allocation, reasoning, risk_flags

        if regime == "risk_off":
            cap = self.config.risk_off_cap
            lifted = False
            if froth is not None and froth <= self.config.froth_calm_threshold \
                    and self.config.froth_calm_cap > cap:
                cap = self.config.froth_calm_cap
                lifted = True
            if allocation > cap:
                why = ("froth score %.0f <= %.0f, cap lifted"
                       % (froth, self.config.froth_calm_threshold)) if lifted else "risk-off"
                self._log_override("risk_off",
                                   f"allocation capped {allocation:.2%} \u2192 {cap:.0%} ({why})")
                return (action, confidence, cap,
                        [f"Allocation capped to {cap:.0%} (risk-off regime{', cap lifted on low froth' if lifted else ''})"] + reasoning[:4],
                        risk_flags + ["risk_off_cap" if not lifted else "risk_off_cap_lifted"])
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_6_valuation(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        fwd_pe = context.get("forward_pe")
        if fwd_pe is not None and fwd_pe > 0:
            hist_pe = context.get("historical_pe_5y")
            if hist_pe and hist_pe > 0 and (fwd_pe / hist_pe) > self.config.pe_vs_history_ceiling:
                self._log_override("valuation", f"P/E {fwd_pe:.1f}x vs hist {hist_pe:.1f}x = {fwd_pe/hist_pe:.2f}x ceiling")
                if action == "BUY":
                    return ("HOLD", confidence, 0.0,
                            [f"HOLD \u2014 P/E {fwd_pe:.1f}x exceeds {self.config.pe_vs_history_ceiling:.0f}x historical ceiling"] + reasoning[:4],
                            risk_flags + ["expensive_vs_history"])
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_7_dispersion(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        dispersion = context.get("analyst_dispersion")
        if dispersion is not None and dispersion > self.config.max_dispersion and action == "BUY":
            self._log_override("dispersion", f"analyst dispersion {dispersion:.2f} > {self.config.max_dispersion}")
            return ("HOLD", confidence, 0.0,
                    [f"HOLD \u2014 analyst dispersion {dispersion:.2f} exceeds {self.config.max_dispersion} threshold"] + reasoning[:4],
                    risk_flags + ["high_dispersion"])
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_8_name_cap(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        current_position_value: float, portfolio_value: float,
    ) -> tuple:
        current_weight = current_position_value / max(portfolio_value, 1.0)
        if current_weight > self.config.max_name_weight:
            self._log_override("name_cap", f"current weight {current_weight:.2%} > {self.config.max_name_weight:.0%}")
            if action == "BUY":
                new_alloc = max(0.0, allocation - (current_weight - self.config.max_name_weight))
                new_alloc = max(0.0, min(allocation, new_alloc))
                return (action, confidence, new_alloc, reasoning + [f"Weight capped at {self.config.max_name_weight:.0%}"], risk_flags)
        return action, confidence, allocation, reasoning, risk_flags

    def _gate_9_liquidity(
        self, action: str, confidence: float, allocation: float,
        reasoning: list[str], risk_flags: list[str],
        features: dict, context: dict,
    ) -> tuple:
        if action != "BUY":
            return action, confidence, allocation, reasoning, risk_flags
        avg_daily_volume = context.get("avg_daily_volume")
        current_price = context.get("current_price")
        if avg_daily_volume and current_price and avg_daily_volume > 0 and current_price > 0:
            position_value = portfolio_value = context.get("portfolio_value", 100_000)
            if position_value > 0:
                implied_shares = (allocation * portfolio_value) / current_price
                days_to_liquidate = implied_shares / avg_daily_volume if avg_daily_volume > 0 else 99
                if days_to_liquidate > 1.0 / self.config.min_adv_multiple:
                    max_adv_frac = self.config.min_adv_multiple * avg_daily_volume * current_price / portfolio_value
                    new_alloc = min(allocation, max_adv_frac)
                    if new_alloc < allocation:
                        self._log_override("liquidity", f"position {allocation:.2%} > {new_alloc:.2%} (ADV constraint)")
                        return (action, confidence, new_alloc,
                                reasoning + [f"Allocation cut to {new_alloc:.1%} (ADV constraint)"],
                                risk_flags + ["liquidity_constrained"])
        return action, confidence, allocation, reasoning, risk_flags


# ------------------------------------------------------------------
# Portfolio-level pre-trade check
# ------------------------------------------------------------------

@dataclass
class PortfolioLimitConfig:
    max_gross_exposure: float = 1.0
    max_single_name: float = 0.10
    max_sector_weight: float = 0.35


def enforce_portfolio_limits(
    decisions: list[tuple[str, FinalTrade, float]],
    config: PortfolioLimitConfig | None = None,
) -> list[FinalTrade]:
    """Apply portfolio-level limits across all ticker decisions.

    Args:
        decisions: List of (ticker, FinalTrade, last_price) tuples.
        config: Portfolio limit configuration.

    Returns:
        Adjusted FinalTrade list with portfolio-level constraints applied.
    """
    cfg = config or PortfolioLimitConfig()
    if not decisions:
        return [d for _, d, _ in decisions]

    trades = list(decisions)
    total_alloc = sum(max(0.0, d.allocation) for _, d, _ in trades)

    if total_alloc <= cfg.max_gross_exposure:
        return [d for _, d, _ in trades]

    scale = cfg.max_gross_exposure / total_alloc
    logger.info(
        "Portfolio gross exposure %.0f%% exceeds %.0f%% \u2014 scaling all allocations by %.2f",
        total_alloc * 100, cfg.max_gross_exposure * 100, scale,
    )

    adjusted: list[FinalTrade] = []
    for _, d, _ in trades:
        new_alloc = round(d.allocation * scale, 4)
        new_alloc = min(new_alloc, cfg.max_single_name)
        adjusted.append(FinalTrade(
            action=d.action,
            confidence=d.confidence,
            allocation=new_alloc,
            reasoning=d.reasoning + [f"Portfolio scale: {scale:.2f}"],
            risk_flags=d.risk_flags + ["portfolio_scaled"],
            risk_overrides=d.risk_overrides + [f"portfolio_scale_{scale:.2f}"],
        ))

    return adjusted
