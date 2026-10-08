"""Market-level risk-off regime — replaces the per-ticker price proxy.

The legacy ``mdu.features._macro_regime`` derived "market regime" from each
ticker's own trend. This module computes one regime per day from genuine
market inputs:

- index trend (3m / 12m total return),
- 2s10s yield-curve shape,
- funding-stress percentile (SOFR-EFFR spread vs its own history).

Rules are documented heuristics, deliberately conservative: the regime only
flips to ``risk_off`` on tangible stress evidence.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

_TREND_3M = 63      # business days
_TREND_12M = 252


@dataclass(frozen=True)
class MarketRegime:
    """One-day market-wide regime classification with its evidence."""

    regime: str  # risk_on | risk_off | neutral
    trend_3m: float | None = None
    trend_12m: float | None = None
    spread_2s10s: float | None = None
    funding_stress_pctile: float | None = None
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime,
            "trend_3m": self.trend_3m,
            "trend_12m": self.trend_12m,
            "spread_2s10s": self.spread_2s10s,
            "funding_stress_pctile": self.funding_stress_pctile,
            "reasons": self.reasons,
        }


def trailing_return(close: pd.Series, window: int) -> float | None:
    close = close.dropna()
    if len(close) < window + 1 or close.iloc[-window - 1] <= 0:
        return None
    return float(close.iloc[-1] / close.iloc[-window - 1] - 1.0)


def funding_stress_percentile(funding_spread: pd.Series) -> float | None:
    """Latest SOFR-EFFR spread as a percentile of its trailing 10y history."""
    s = funding_spread.dropna()
    if len(s) < 250:
        return None
    cutoff = s.index.max() - pd.Timedelta(days=365 * 10)
    window = s[s.index >= cutoff]
    if len(window) < 100:
        return None
    return float((window < s.iloc[-1]).mean()) * 100.0


def compute_market_regime(
    index_close: pd.Series,
    spread_2s10s: float | None = None,
    funding_spread: pd.Series | None = None,
) -> MarketRegime:
    """Classify the current market-wide regime.

    risk_off when any of:
      - 3m index return <= -3%,
      - funding stress in the top decile of its 10y range while the curve
        is flat/inverted,
      - inverted curve AND 3m return <= -1%.

    risk_on when 12m return > +5% and the curve is not inverted.
    Otherwise neutral.
    """
    trend_3m = trailing_return(index_close, _TREND_3M)
    trend_12m = trailing_return(index_close, _TREND_12M)
    stress_pct = funding_stress_percentile(funding_spread) if funding_spread is not None else None

    reasons: list[str] = []
    regime = "neutral"

    risk_off_triggers: list[bool] = []
    if trend_3m is not None and trend_3m <= -0.03:
        reasons.append(f"index 3m return {trend_3m:+.1%} <= -3%")
        risk_off_triggers.append(True)
    if stress_pct is not None and stress_pct >= 90 and (spread_2s10s is None or spread_2s10s <= 0.5):
        reasons.append(f"funding stress p{stress_pct:.0f} with flat/inverted curve")
        risk_off_triggers.append(True)
    if spread_2s10s is not None and spread_2s10s < 0 and trend_3m is not None and trend_3m <= -0.01:
        reasons.append(f"inverted 2s10s ({spread_2s10s:+.2f}) with {trend_3m:+.1%} 3m")
        risk_off_triggers.append(True)

    if any(risk_off_triggers):
        regime = "risk_off"

    if (
        regime == "neutral"
        and trend_12m is not None
        and trend_12m > 0.05
        and (spread_2s10s is None or spread_2s10s > 0)
    ):
        regime = "risk_on"
        reasons.append(f"index 12m return {trend_12m:+.1%}, curve not inverted")

    if not reasons:
        reasons.append("no strong regime evidence")

    return MarketRegime(
        regime=regime,
        trend_3m=trend_3m,
        trend_12m=trend_12m,
        spread_2s10s=spread_2s10s,
        funding_stress_pctile=stress_pct,
        reasons=reasons,
    )
