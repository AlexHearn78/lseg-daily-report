from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# ---- Weights used by the live analyst system ----
# These are the ONLY weights used by both live and backtest.
# Any analysis that varies these weights is a different strategy.
WEIGHTS: dict[str, float] = {
    "valuation": 0.30,
    "trend": 0.20,
    "risk": 0.15,
    "macro": 0.15,
    "news": 0.10,
    "vol": 0.10,
}

BUY_THRESHOLD = 0.25
SELL_THRESHOLD = -0.25
CONFIDENCE_FLOOR = 0.75
MAX_ALLOCATION = 0.30
MIN_ALLOCATION = 0.05


def score_valuation(
    current_price: float = 0.0,
    consensus_estimates: list[dict] | None = None,
    fundamentals_raw: dict[str, float | None] | None = None,
    shares_outstanding: float | None = None,
) -> tuple[float, list[str]]:
    """Multi-metric valuation score.

    Sub-metrics (each weighted equally, only those with data):
      1. Forward P/E
      2. EV/EBITDA
      3. FCF Yield
      4. Dividend Yield
      5. PEG ratio
      6. Revenue growth (trailing FY)
    """
    reasoning: list[str] = []
    score = 0.0
    signals = 0

    if not consensus_estimates or current_price <= 0:
        return 0.0, ["Valuation: no consensus estimates available"]

    fy1 = consensus_estimates[0]
    fy1_eps = fy1.get("eps_est")
    fy1_ebitda_ps = fy1.get("ebitda_ps")
    fy1_dps = fy1.get("dps_est")
    fy1_fcf_ps = fy1.get("fcf_ps")
    fy1_rev = fy1.get("rev_est_m")
    net_debt: float | None = None
    if fundamentals_raw:
        debt = fundamentals_raw.get("total_debt")
        cash = fundamentals_raw.get("cash")
        if debt is not None and cash is not None:
            net_debt = debt - cash
        if shares_outstanding is None:
            shares_outstanding = fundamentals_raw.get("shares_outstanding")

    # --- sub-metric 1: Forward P/E ---
    if fy1_eps and fy1_eps > 0:
        fwd_pe = current_price / fy1_eps
        pe_score = 0.0
        if fwd_pe < 15:
            pe_score = 1.0
            reasoning.append(f"P/E: {fwd_pe:.1f}x is cheap")
        elif fwd_pe < 25:
            pe_score = 0.3
            reasoning.append(f"P/E: {fwd_pe:.1f}x is reasonable")
        elif fwd_pe < 35:
            pe_score = -0.2
            reasoning.append(f"P/E: {fwd_pe:.1f}x is elevated")
        else:
            pe_score = -0.8
            reasoning.append(f"P/E: {fwd_pe:.1f}x is expensive")
        score += pe_score
        signals += 1

    # --- sub-metric 2: EV/EBITDA ---
    ev: float | None = None
    if fy1_ebitda_ps and fy1_ebitda_ps > 0:
        if shares_outstanding and shares_outstanding > 0:
            mkt_cap = current_price * shares_outstanding
            if net_debt is not None:
                ev = mkt_cap + net_debt
            else:
                ev = mkt_cap
        elif net_debt is not None:
            ev = current_price + net_debt
        if ev is not None and ev > 0:
            total_ebitda = fy1_ebitda_ps * (shares_outstanding or 1)
            if total_ebitda > 0:
                ev_ebitda = ev / total_ebitda
                if ev_ebitda < 8:
                    score += 0.8
                    reasoning.append(f"EV/EBITDA: {ev_ebitda:.1f}x is attractive")
                elif ev_ebitda < 12:
                    score += 0.2
                    reasoning.append(f"EV/EBITDA: {ev_ebitda:.1f}x is fair")
                elif ev_ebitda < 18:
                    score -= 0.2
                    reasoning.append(f"EV/EBITDA: {ev_ebitda:.1f}x is elevated")
                else:
                    score -= 0.6
                    reasoning.append(f"EV/EBITDA: {ev_ebitda:.1f}x is expensive")
                signals += 1

    # --- sub-metric 3: FCF Yield ---
    if fy1_fcf_ps and fy1_fcf_ps > 0 and current_price > 0:
        fcf_yield = fy1_fcf_ps / current_price
        if fcf_yield > 0.06:
            score += 0.7
            reasoning.append(f"FCF yield: {fcf_yield:.1%} is strong")
        elif fcf_yield > 0.03:
            score += 0.2
            reasoning.append(f"FCF yield: {fcf_yield:.1%} is adequate")
        elif fcf_yield > 0:
            score -= 0.1
            reasoning.append(f"FCF yield: {fcf_yield:.1%} is thin")
        else:
            score -= 0.3
        signals += 1

    # --- sub-metric 4: Dividend Yield ---
    if fy1_dps and fy1_dps > 0 and current_price > 0:
        div_yield = fy1_dps / current_price
        if div_yield > 0.04:
            score += 0.5
            reasoning.append(f"Div yield: {div_yield:.1%} is attractive")
        elif div_yield > 0.02:
            score += 0.1
        elif div_yield > 0:
            score -= 0.1
        else:
            score -= 0.2
        signals += 1

    # --- sub-metric 5: PEG ratio ---
    if fy1_eps and fy1_eps > 0 and len(consensus_estimates) >= 3:
        eps_last = consensus_estimates[-1].get("eps_est")
        if eps_last and eps_last > 0 and fy1_eps > 0:
            periods = max(1, len(consensus_estimates) - 1)
            cagr = (fy1_eps / eps_last) ** (1.0 / periods) - 1
            if cagr > 0:
                peg = (current_price / fy1_eps) / (cagr * 100)
                if peg < 1.0:
                    score += 0.4
                    reasoning.append(f"PEG: {peg:.1f}x is attractive (growth supports multiple)")
                elif peg > 2.0:
                    score -= 0.3
                    reasoning.append(f"PEG: {peg:.1f}x is rich vs growth")
                signals += 1

    # --- sub-metric 6: Revenue growth ---
    if len(consensus_estimates) >= 2:
        rev_y0 = consensus_estimates[0].get("rev_est_m")
        rev_y1 = consensus_estimates[1].get("rev_est_m")
        if rev_y0 and rev_y1 and rev_y1 > 0:
            growth = (rev_y0 - rev_y1) / rev_y1
            if growth > 0.15:
                score += 0.5
                reasoning.append(f"Rev growth: {growth:.0%} is strong")
            elif growth > 0.05:
                score += 0.2
            else:
                score -= 0.2
            signals += 1

    if signals == 0:
        return 0.0, ["Valuation: insufficient data to score"]

    return max(-1.0, min(1.0, score / signals)), reasoning


def score_trend(
    ret_1m_pct: float | None = None,
    ret_3m_pct: float | None = None,
    range_position_pct: float = 50.0,
) -> tuple[float, list[str]]:
    """Trend/momentum score from returns and 52-week range position."""
    reasoning: list[str] = []
    score = 0.0
    signals = 0

    if ret_1m_pct is not None:
        if ret_1m_pct > 5:
            score += 0.5
            reasoning.append(f"Trend: +{ret_1m_pct:.1f}% 1m momentum is strong")
        elif ret_1m_pct > 0:
            score += 0.2
        elif ret_1m_pct > -10:
            score -= 0.1
            reasoning.append(f"Trend: {ret_1m_pct:.1f}% 1m \u2014 mild pullback")
        elif ret_1m_pct > -20:
            score -= 0.4
            reasoning.append(f"Trend: {ret_1m_pct:.1f}% 1m \u2014 significant correction, potential opportunity")
        else:
            score -= 0.7
            reasoning.append(f"Trend: {ret_1m_pct:.1f}% 1m \u2014 severe decline, avoid catching falling knife")
        signals += 1

    if ret_3m_pct is not None:
        if ret_3m_pct > 5:
            score += 0.3
        elif ret_3m_pct < -10:
            score -= 0.3
        signals += 1

    if range_position_pct > 80:
        score += 0.4
        reasoning.append(f"Trend: price near 52w high ({range_position_pct:.0f}% of range)")
    elif range_position_pct < 20:
        score -= 0.2
        reasoning.append(f"Trend: price near 52w low ({range_position_pct:.0f}% of range)")
    signals += 1

    return max(-1.0, min(1.0, score / max(signals, 1))), reasoning


def score_risk(
    max_drawdown_pct: float = 0.0,
    vol_21d_annualised_pct: float | None = None,
) -> tuple[float, list[str], list[str]]:
    """Risk score from drawdown and volatility. Returns (score, reasoning, risk_flags)."""
    reasoning: list[str] = []
    risk_flags: list[str] = []
    score = 0.0
    signals = 0

    dd_dec = max_drawdown_pct / 100.0
    if max_drawdown_pct < 0:
        if max_drawdown_pct > -10.0:
            score += 0.5
        elif max_drawdown_pct > -20.0:
            score -= 0.1
            reasoning.append(f"Risk: drawdown of {max_drawdown_pct:.1f}% \u2014 elevated but manageable")
        elif max_drawdown_pct > -35.0:
            score -= 0.4
            reasoning.append(f"Risk: drawdown of {max_drawdown_pct:.1f}% is deep")
            risk_flags.append(f"drawdown_{dd_dec:.0%}")
        else:
            score -= 0.8
            reasoning.append(f"Risk: drawdown of {max_drawdown_pct:.1f}% is extreme")
            risk_flags.append(f"drawdown_{dd_dec:.0%}")
        signals += 1

    if vol_21d_annualised_pct is not None:
        if vol_21d_annualised_pct > 60:
            score -= 0.4
            reasoning.append(f"Risk: 21d vol {vol_21d_annualised_pct:.0f}% is very high")
            risk_flags.append("elevated_vol")
        elif vol_21d_annualised_pct > 40:
            score -= 0.2
        elif vol_21d_annualised_pct < 20:
            score += 0.2
        signals += 1

    if signals == 0:
        return 0.0, [], risk_flags

    return max(-1.0, min(1.0, score / signals)), reasoning, risk_flags


def score_macro(
    curve_shape: str = "flat",
    spread_2s10s_bps: float = 0.0,
) -> tuple[float, list[str]]:
    """Macro score from yield curve regime."""
    reasoning: list[str] = []

    if curve_shape == "inverted":
        reasoning.append(f"Macro: yield curve inverted (2s10s={spread_2s10s_bps:.1f}bps) \u2014 recession signal")
        return -0.5, reasoning
    elif curve_shape == "steep":
        reasoning.append(f"Macro: yield curve steepening (2s10s={spread_2s10s_bps:.1f}bps) \u2014 growth optimism")
        return 0.4, reasoning
    else:
        reasoning.append(f"Macro: yield curve flat (2s10s={spread_2s10s_bps:.1f}bps) \u2014 neutral")
        return 0.0, reasoning


def score_news(
    shock_detected: bool = False,
    total_headlines: int = 0,
) -> tuple[float, list[str], list[str]]:
    """News sentiment score. Returns (score, reasoning, risk_flags)."""
    reasoning: list[str] = []
    risk_flags: list[str] = []

    if shock_detected:
        reasoning.append("News: shock keywords detected \u2014 cautious")
        risk_flags.append("news_shock")
        return -0.4, reasoning, risk_flags

    if total_headlines == 0:
        return 0.0, ["News: no headlines in window \u2014 neutral"], risk_flags

    if total_headlines <= 3:
        score = 0.1
    elif total_headlines <= 10:
        score = 0.0
    else:
        score = -0.1

    reasoning.append(f"News: {total_headlines} headlines, no shock signals")
    return score, reasoning, risk_flags


def score_vol(
    atm_iv_1m_pct: float | None = None,
) -> tuple[float, list[str]]:
    """Vol surface score from ATM implied vol."""
    reasoning: list[str] = []

    if atm_iv_1m_pct is None:
        return 0.0, []

    if atm_iv_1m_pct > 60:
        reasoning.append(f"Vol: ATM IV {atm_iv_1m_pct:.0f}% \u2014 elevated, stress pricing")
        return -0.3, reasoning
    elif atm_iv_1m_pct > 40:
        reasoning.append(f"Vol: ATM IV {atm_iv_1m_pct:.0f}% \u2014 moderately elevated")
        return -0.1, reasoning
    elif atm_iv_1m_pct < 20:
        reasoning.append(f"Vol: ATM IV {atm_iv_1m_pct:.0f}% \u2014 low, complacent")
        return 0.2, reasoning
    else:
        return 0.0, reasoning


def compute_composite(
    scores: dict[str, float],
    weights: dict[str, float] | None = None,
    active_dims: set[str] | None = None,
) -> float:
    """Weighted composite score from individual dimension scores.

    When ``active_dims`` is provided, weights are renormalized among only
    those dimensions. This allows the backtest to produce comparable
    composites when only a subset of dimensions are available.
    """
    w = weights or WEIGHTS
    if active_dims is not None:
        w_active = {k: w[k] for k in w if k in active_dims}
        total = sum(w_active.values())
        if total <= 0:
            return 0.0
        scale = 1.0 / total
        weighted = sum(w_active[k] * scores.get(k, 0.0) for k in w_active)
        weighted *= scale
    else:
        weighted = sum(w[k] * scores.get(k, 0.0) for k in w)
    return max(-1.0, min(1.0, weighted))


def composite_to_decision(
    composite: float,
    buy_threshold: float = BUY_THRESHOLD,
    sell_threshold: float = SELL_THRESHOLD,
    confidence_floor: float = CONFIDENCE_FLOOR,
    max_allocation: float = MAX_ALLOCATION,
    min_allocation: float = MIN_ALLOCATION,
) -> tuple[str, float, float]:
    """Map composite score to (action, confidence, allocation).

    Confidence maps so that the buy_threshold composite value
    produces confidence = confidence_floor, making executable
    BUYs reachable under the risk override layer.
    """
    if composite >= buy_threshold:
        action = "BUY"
        raw_conf = confidence_floor + (1.0 - confidence_floor) * (
            composite - buy_threshold
        ) / (1.0 - buy_threshold)
        raw_alloc = min_allocation + (max_allocation - min_allocation) * (
            composite - buy_threshold
        ) / (1.0 - buy_threshold)
    elif composite <= sell_threshold:
        action = "SELL"
        raw_conf = confidence_floor + (1.0 - confidence_floor) * (
            abs(composite) - abs(sell_threshold)
        ) / (1.0 - abs(sell_threshold))
        raw_alloc = 0.0
    else:
        action = "HOLD"
        raw_conf = 0.5 + (composite / (buy_threshold * 2))
        raw_alloc = 0.0

    confidence = round(max(0.05, min(0.98, raw_conf)), 2)
    allocation = round(max(0.0, min(max_allocation, raw_alloc)), 2)
    return action, confidence, allocation
