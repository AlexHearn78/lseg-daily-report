from __future__ import annotations

import datetime as dt
import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

FEATURE_KEYS = [
    "trend_3m",
    "trend_12m",
    "volatility_30d",
    "volatility_12m",
    "max_drawdown_6m",
    "rate_trend",
    "inflation_trend",
    "macro_regime",
    "liquidity_conditions",
    "news_shock_flag",
]

FeatureVector = dict[str, float | str | bool]


def compute_features(
    prices: pd.DataFrame,
    yield_curve: pd.DataFrame | None = None,
    news_headlines: list[dict] | None = None,
    as_of: dt.datetime | None = None,
    market_context: dict | None = None,
) -> FeatureVector:
    """Compute the per-ticker feature vector.

    ``market_context`` (optional) carries the market-wide regime from
    ``lseg_quant.regime.daily``: keys ``regime``, ``froth_composite``,
    ``froth_band``, ``froth_velocity``. When supplied it replaces the
    legacy per-ticker price proxy for ``macro_regime``.
    """
    if as_of is None:
        as_of = pd.Timestamp.now()

    if prices.empty or "close" not in prices.columns:
        vector = _default_vector("no_price_data")
        _apply_market_context(vector, market_context)
        return vector

    close = prices["close"].dropna().values
    if len(close) < 2:
        vector = _default_vector("insufficient_price_data")
        _apply_market_context(vector, market_context)
        return vector

    # --- Trend (trailing returns) ---
    trend_3m = _trailing_return(close, 63)
    trend_12m = _trailing_return(close, 252)

    # --- Volatility ---
    log_returns = np.diff(np.log(close[close > 0]))
    min_window = min(21, len(log_returns))
    vol_30d = float(np.std(log_returns[-min_window:]) * np.sqrt(252))
    vol_12m = float(np.std(log_returns) * np.sqrt(252))

    # --- Max drawdown (6m ideally, fallback to full series) ---
    window_6m = min(126, len(close))
    max_dd_6m = _max_drawdown(close[-window_6m:])

    # --- Rate / inflation / regime / liquidity ---
    rate_trend = _rate_trend(yield_curve)
    inflation_trend = "sticky"
    macro_regime = _macro_regime(trend_3m, trend_12m)
    liquidity_conditions = _liquidity_conditions(yield_curve)
    news_shock_flag = _news_shock_flag(news_headlines)

    vector: FeatureVector = {
        "trend_3m": round(trend_3m, 4),
        "trend_12m": round(trend_12m, 4),
        "volatility_30d": round(vol_30d, 4),
        "volatility_12m": round(vol_12m, 4),
        "max_drawdown_6m": round(max_dd_6m, 4),
        "rate_trend": rate_trend,
        "inflation_trend": inflation_trend,
        "macro_regime": macro_regime,
        "liquidity_conditions": liquidity_conditions,
        "news_shock_flag": news_shock_flag,
    }
    _apply_market_context(vector, market_context)
    return vector


def _apply_market_context(vector: FeatureVector,
                          market_context: dict | None) -> None:
    """Overlay market-wide regime + froth score onto the feature vector."""
    if not market_context:
        return
    if market_context.get("regime"):
        vector["macro_regime"] = str(market_context["regime"])
    for key in ("froth_composite", "froth_band", "froth_velocity"):
        if market_context.get(key) is not None:
            vector[key] = market_context[key]


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _default_vector(reason: str) -> FeatureVector:
    logger.warning("Using default feature vector: %s", reason)
    return {
        "trend_3m": 0.0,
        "trend_12m": 0.0,
        "volatility_30d": 0.0,
        "volatility_12m": 0.0,
        "max_drawdown_6m": 0.0,
        "rate_trend": "flat",
        "inflation_trend": "sticky",
        "macro_regime": "neutral",
        "liquidity_conditions": "neutral",
        "news_shock_flag": False,
    }


def _trailing_return(prices: np.ndarray, window: int) -> float:
    if len(prices) < window + 1:
        return float((prices[-1] / prices[0]) - 1) if prices[0] > 0 else 0.0
    return float((prices[-1] / prices[-window - 1]) - 1)


def _max_drawdown(prices: np.ndarray) -> float:
    if len(prices) < 2:
        return 0.0
    peak = np.maximum.accumulate(prices)
    dd = (prices - peak) / peak
    return float(np.min(dd))


def _rate_trend(curve: pd.DataFrame | None) -> str:
    if curve is None or curve.empty:
        return "flat"
    try:
        if "tenor" in curve.columns and "rate" in curve.columns:
            rates_by_tenor = dict(zip(curve["tenor"], curve["rate"]))
        elif "definition" in curve.columns:
            rates_by_tenor = {}
            for _, row in curve.iterrows():
                d = row.get("definition", {})
                tenor = d.get("tenor", "") if isinstance(d, dict) else ""
                q = row.get("quote", {})
                v = q.get("values", {}) if isinstance(q, dict) else {}
                bid = v.get("bid") or v.get("value")
                if isinstance(bid, dict):
                    bid = bid.get("value")
                if bid is not None:
                    rates_by_tenor[tenor] = float(bid)
        else:
            return "flat"

        if "2Y" in rates_by_tenor and "10Y" in rates_by_tenor:
            spread = rates_by_tenor["10Y"] - rates_by_tenor["2Y"]
            if spread < 0:
                return "inverted"
            return "rising" if spread > 0.5 else "flat"
    except Exception:
        pass
    return "flat"


def _macro_regime(trend_3m: float, trend_12m: float) -> str:
    if trend_12m > 0.05:
        return "risk_on"
    if trend_3m < -0.03:
        return "risk_off"
    return "neutral"


def _liquidity_conditions(curve: pd.DataFrame | None) -> str:
    if curve is None or curve.empty:
        return "neutral"
    try:
        if "tenor" in curve.columns and "rate" in curve.columns:
            match = curve[curve["tenor"] == "3M"]
            if not match.empty:
                rate = float(match.iloc[0]["rate"])
                return "tight" if rate > 5.0 else "loose" if rate < 2.0 else "neutral"
        else:
            for _, row in curve.iterrows():
                d = row.get("definition", {})
                tenor = d.get("tenor", "") if isinstance(d, dict) else ""
                if tenor == "3M":
                    q = row.get("quote", {})
                    if isinstance(q, dict):
                        v = q.get("values", {})
                        if isinstance(v, dict):
                            bid = v.get("bid") or v.get("value")
                            if isinstance(bid, dict):
                                bid = bid.get("value")
                            if bid is not None:
                                rate = float(bid)
                                return "tight" if rate > 5.0 else "loose" if rate < 2.0 else "neutral"
    except Exception:
        pass
    return "neutral"


def _news_shock_flag(headlines: list[dict] | None) -> bool:
    if not headlines:
        return False
    shock_keywords = [
        "crash", "plunge", "meltdown", "recession", "crisis",
        "bank run", "default", "bailout", "emergency", "flash crash",
    ]
    for h in headlines:
        text = (h.get("headlineText") or h.get("headline") or "").lower()
        if any(kw in text for kw in shock_keywords):
            return True
    return False
