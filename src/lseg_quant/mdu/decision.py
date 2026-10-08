from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from lseg_quant.mdu.features import FeatureVector

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Data classes
# ------------------------------------------------------------------

@dataclass
class LLMDecision:
    action: str  # BUY | SELL | HOLD
    confidence: float  # 0.0–1.0
    allocation: float  # 0.0–1.0
    reasoning: list[str]  # max 5 bullets
    risk_flags: list[str]


@dataclass
class FinalTrade:
    action: str
    confidence: float
    allocation: float
    reasoning: list[str]
    risk_flags: list[str]
    risk_overrides: list[str]  # which risk rules overrode the LLM


# ------------------------------------------------------------------
# Risk Engine (hard rules — overrides LLM)
# ------------------------------------------------------------------

def validate_decision(
    llm_decision: LLMDecision,
    features: FeatureVector,
    current_position_pct: float = 0.0,
) -> FinalTrade:
    """Apply hard risk rules on top of LLM decision.

    Confidence floor is calibrated to the scoring module's mapping
    (composite=0.25 → confidence=0.75) so executable BUYs are reachable.

    Returns a FinalTrade with risk_overrides populated when rules fire.
    """
    overrides: list[str] = []
    action = llm_decision.action
    confidence = llm_decision.confidence
    allocation = llm_decision.allocation
    reasoning = list(llm_decision.reasoning)
    risk_flags = list(llm_decision.risk_flags)

    # Rule 1: confidence floor
    if confidence < 0.75:
        overrides.append(f"confidence_{confidence:.2f}_below_0.75")
        action = "HOLD"
        allocation = 0.0
        reasoning = [f"HOLD \u2014 confidence {confidence:.2f} below 0.75 threshold"] + reasoning[:4]

    # Rule 2: news shock
    if features.get("news_shock_flag"):
        overrides.append("news_shock_flag_true")
        action = "HOLD"
        allocation = 0.0
        reasoning = ["HOLD \u2014 news shock detected"] + reasoning[:4]

    # Rule 3: risk-off max allocation
    if features.get("macro_regime") == "risk_off":
        max_allowed = 0.3
        if allocation > max_allowed:
            overrides.append(f"risk_off_cap_{allocation}_to_{max_allowed}")
            allocation = max_allowed
            reasoning = [f"Allocation capped to {max_allowed} (risk-off regime)"] + reasoning[:4]

    # Rule 4: max drawdown
    max_dd = features.get("max_drawdown_6m", 0.0)
    if isinstance(max_dd, (int, float)) and max_dd < -0.15:
        overrides.append(f"max_drawdown_{max_dd:.4f}_below_-0.15")
        action = "HOLD"
        allocation = 0.0
        reasoning = [f"HOLD \u2014 max drawdown {max_dd:.2%} exceeds -15% threshold"] + reasoning[:4]

    # Rule 5: action validation
    if action not in ("BUY", "SELL", "HOLD"):
        overrides.append(f"invalid_action_{action}")
        action = "HOLD"
        allocation = 0.0
        reasoning = [f"HOLD \u2014 '{action}' is not a valid action"] + reasoning[:4]

    # Rule 6: allocation cap
    if allocation > 1.0:
        overrides.append(f"allocation_capped_from_{allocation}_to_1.0")
        allocation = 1.0

    return FinalTrade(
        action=action,
        confidence=confidence,
        allocation=max(0.0, min(1.0, allocation)),
        reasoning=reasoning[:5],
        risk_flags=risk_flags,
        risk_overrides=overrides,
    )


# ------------------------------------------------------------------
# LLM Decision Engine (prompt builder + parser)
# ------------------------------------------------------------------

LLM_SYSTEM_PROMPT = """You are a quantitative analyst for a systematic equity trading strategy.

Your input is a structured 5-section equity research memo with market data,
valuation estimates, yield curves, news headlines, and volatility surface data.

Your ONLY output is a JSON trading decision.

RULES:
- Use the data provided to support your reasoning. Reference specific numbers.
- You are an analyst, not a trader \u2014 you provide signals, Python executes them.
- Be conservative. HOLD is a valid and common decision.
- Do NOT invent data or reference variables not present in the prompt.

OUTPUT FORMAT (strict JSON):
{
  "action": "BUY" | "SELL" | "HOLD",
  "confidence": 0.0-1.0,
  "allocation": 0.0-1.0,
  "reasoning": ["max 5 concise bullets with specific numbers from the data"],
  "risk_flags": ["list any concerns"]
}"""


def build_llm_prompt(
    features: FeatureVector,
    current_action: str = "HOLD",
    current_allocation: float = 0.0,
) -> str:
    """Build the user message for the LLM from feature vector."""
    feature_json = json.dumps(features, indent=2)
    return f"""Current position: {current_action} at {current_allocation:.0%} allocation.

Feature vector:
{feature_json}

What is your trading decision for the next month?"""


def parse_llm_response(raw: str) -> LLMDecision:
    """Parse LLM JSON response into an LLMDecision."""
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1:
        logger.warning("No JSON found in LLM response; defaulting to HOLD")
        return LLMDecision(action="HOLD", confidence=0.0, allocation=0.0,
                           reasoning=["LLM response parse failed"], risk_flags=["parse_error"])

    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as e:
        logger.warning("Failed to parse LLM JSON: %s", e)
        return LLMDecision(action="HOLD", confidence=0.0, allocation=0.0,
                           reasoning=[f"JSON parse error: {e}"], risk_flags=["parse_error"])

    action = str(data.get("action", "HOLD")).upper()
    if action not in ("BUY", "SELL", "HOLD"):
        action = "HOLD"

    return LLMDecision(
        action=action,
        confidence=float(data.get("confidence", 0.0)),
        allocation=float(data.get("allocation", 0.0)),
        reasoning=[str(r) for r in data.get("reasoning", [])][:5],
        risk_flags=[str(f) for f in data.get("risk_flags", [])],
    )
