from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from lseg_quant.mdu.research import ResearchContext
from lseg_quant.mdu.scoring import (
    compute_composite,
    composite_to_decision,
    score_news,
    score_risk,
    score_trend,
    score_macro,
    score_valuation,
    score_vol,
)

logger = logging.getLogger(__name__)

DIMENSION_LABELS = ["valuation", "trend", "risk", "macro", "news", "vol"]


@dataclass
class AnalystDecision:
    action: str
    confidence: float
    allocation: float
    reasoning: list[str]
    risk_flags: list[str]
    dimension_scores: dict[str, float] = field(default_factory=dict)
    composite: float = 0.0


def analyze(context: ResearchContext) -> AnalystDecision:
    """Automated rules-based analyst.

    Extracts data from the research context, scores each dimension
    using the unified scoring module, and produces a BUY/HOLD/SELL
    decision with the same weights used by the backtest.
    """
    data = context.build()
    ticker = context.ticker

    reasoning: list[str] = []
    risk_flags: list[str] = []

    core = data["modules"].get("Module 1 \u2014 Equity Core", {})
    val = data["modules"].get("Module 2 \u2014 Valuation", {})
    yield_mod = data["modules"].get("Module 3 \u2014 Government Yield Curves", {})
    vol_mod = data["modules"].get("Module 6 \u2014 Equity Volatility Surface", {})
    news = data.get("news", {})

    # --- 1. Valuation Score ---
    current_price = core.get("price", {}).get("current", 0.0)
    estimates = val.get("consensus_estimates", [])
    fundamentals_raw = val.get("fundamentals_raw", {})
    shares_out = fundamentals_raw.get("shares_outstanding")
    val_score, val_reasoning = score_valuation(
        current_price=current_price,
        consensus_estimates=estimates,
        fundamentals_raw=fundamentals_raw,
        shares_outstanding=shares_out,
    )
    reasoning.extend(val_reasoning)

    # --- 2. Trend Score ---
    ret_1m = core.get("returns", {}).get("1m")
    ret_3m = core.get("returns", {}).get("3m")
    range_pos = core.get("price", {}).get("range_position_pct", 50.0)
    trend_score_val, trend_reasoning = score_trend(ret_1m, ret_3m, range_pos)
    reasoning.extend(trend_reasoning)

    # --- 3. Risk Score ---
    max_dd = core.get("max_drawdown", 0.0)
    vol_21d = core.get("volatility", {}).get("21d_annualised")
    risk_score_val, risk_reasoning, risk_notes = score_risk(max_dd, vol_21d)
    reasoning.extend(risk_reasoning)
    risk_flags.extend(risk_notes)

    # --- 4. Macro Score ---
    metrics = yield_mod.get("metrics", {})
    curve_shape = metrics.get("curve_shape", "flat")
    spread = metrics.get("2s10s_spread", 0.0)
    macro_score_val, macro_reasoning = score_macro(curve_shape, spread)
    reasoning.extend(macro_reasoning)

    # --- 5. News Score ---
    shock = news.get("shock_detected", False)
    count = news.get("total_headlines", 0)
    news_score_val, news_reasoning, news_notes = score_news(shock, count)
    reasoning.extend(news_reasoning)
    risk_flags.extend(news_notes)

    # --- 6. Vol Score ---
    atm_iv = vol_mod.get("atm_iv_1m")
    vol_score_val, vol_reasoning = score_vol(atm_iv)
    reasoning.extend(vol_reasoning)

    # --- Composite ---
    scores = {
        "valuation": val_score,
        "trend": trend_score_val,
        "risk": risk_score_val,
        "macro": macro_score_val,
        "news": news_score_val,
        "vol": vol_score_val,
    }
    composite = compute_composite(scores)
    action, confidence, allocation = composite_to_decision(composite)

    composite_raw = composite
    reasoning = reasoning[:5]

    components = " | ".join(
        f"{k}={scores[k]:+.2f}" for k in DIMENSION_LABELS
    )
    logger.info(
        "Analyst: %s | conf=%.2f alloc=%.2f | %s | composite=%.2f",
        action, confidence, allocation, components, composite_raw,
    )

    return AnalystDecision(
        action=action,
        confidence=confidence,
        allocation=allocation,
        reasoning=reasoning,
        risk_flags=risk_flags,
        dimension_scores=scores,
        composite=composite,
    )


def format_decision(d: AnalystDecision) -> str:
    return json.dumps({
        "action": d.action,
        "confidence": d.confidence,
        "allocation": d.allocation,
        "reasoning": d.reasoning,
        "risk_flags": d.risk_flags,
    }, indent=2)
