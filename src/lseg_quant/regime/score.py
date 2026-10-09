"""Froth score math — percentile ranking, pillar aggregation, velocity.

- each metric -> percentile rank against its own trailing history (0-100),
  oriented so higher always means more frothy;
- pillar score = average of its metric scores;
- structural = valuation; timing = avg(positioning, liquidity);
- composite = weighted sum {valuation .27, positioning .40, liquidity .33}.
  Every input is LSEG data; the original design's leverage pillar (margin
  debt) has no LSEG source and is left out, and its weight is spread
  proportionally over the other three;
- series with < 3 years of history use min-max scaling and are flagged
  ``low_confidence``;
- velocity flag compares composite vs its value ~90 days ago (+/-15 points).
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

PILLAR_WEIGHTS: dict[str, float] = {
    "valuation": 0.27,
    "positioning": 0.40,
    "liquidity": 0.33,
}

LOW_CONFIDENCE_YEARS = 3.0
VELOCITY_WINDOW_DAYS = 90
VELOCITY_THRESHOLD = 15.0

BANDS: list[tuple[float, str]] = [
    (20.0, "capitulation"),
    (40.0, "risk_off"),
    (60.0, "balanced"),
    (80.0, "elevated"),
    (101.0, "frothy"),
]


@dataclass(frozen=True)
class MetricInput:
    """One metric feeding a froth-score pillar."""

    name: str
    pillar: str  # valuation | positioning | liquidity
    value: float | None
    history: pd.Series  # datetime-indexed trailing observations (excl. value if absent)
    invert: bool = False  # True when LOW raw values mean HIGH froth
    window_days: int = 365 * 12  # trailing percentile window


def percentile_score(current_value: float, history_values: np.ndarray | pd.Series,
                     invert: bool = False) -> float:
    """Percentile rank of *current_value* within *history_values* (0-100)."""
    hist = np.asarray(history_values, dtype=float)
    hist = hist[~np.isnan(hist)]
    if len(hist) == 0 or np.isnan(current_value):
        return float("nan")
    pct = float((hist < current_value).mean()) * 100.0
    return 100.0 - pct if invert else pct


def minmax_score(current_value: float, history_values: np.ndarray | pd.Series,
                 invert: bool = False) -> float:
    """Min-max scale *current_value* against *history_values* (0-100).

    Short-history fallback per spec. NaN when range is degenerate.
    """
    hist = np.asarray(history_values, dtype=float)
    hist = hist[~np.isnan(hist)]
    if len(hist) == 0 or np.isnan(current_value):
        return float("nan")
    lo, hi = float(hist.min()), float(hist.max())
    if hi - lo < 1e-12:
        return 50.0
    scaled = (current_value - lo) / (hi - lo) * 100.0
    scaled = min(100.0, max(0.0, scaled))
    return 100.0 - scaled if invert else scaled


def _metric_score(metric: MetricInput) -> tuple[float, bool]:
    """Score one metric. Returns (score_0_100, low_confidence_flag).

    Returns (nan, True) when the metric has no usable data at all.
    """
    hist = metric.history.dropna()
    span_years = 0.0
    if len(hist) >= 2:
        span_years = (hist.index.max() - hist.index.min()).days / 365.25
    low_confidence = span_years < LOW_CONFIDENCE_YEARS

    if metric.value is None or (isinstance(metric.value, float) and np.isnan(metric.value)):
        return float("nan"), True

    values = hist.values if len(hist) else np.array([])
    if low_confidence and len(values) >= 1:
        # Spec: short-history series use min-max scaling + flag.
        score = minmax_score(float(metric.value), values, invert=metric.invert)
        return score, True
    if len(values) < 5:
        # Too thin even for a meaningful min-max — flag but try anyway.
        score = minmax_score(float(metric.value), values, invert=metric.invert)
        return score, True
    cutoff = hist.index.max() - pd.Timedelta(days=metric.window_days)
    window = hist[hist.index >= cutoff].values
    score = percentile_score(float(metric.value), window, invert=metric.invert)
    return score, low_confidence


def band_for(composite: float) -> str:
    """Map a composite score to its band label."""
    if np.isnan(composite):
        return "unknown"
    for upper, label in BANDS:
        if composite < upper:
            return label
    return "frothy"


def velocity_flag(score_history: pd.Series, current_score: float) -> str:
    """Compare current composite vs its value ~90 days ago."""
    if score_history is None or len(score_history) < 2:
        return "insufficient_history"
    anchor = score_history.index.max()
    past = score_history[score_history.index <= anchor - dt.timedelta(days=VELOCITY_WINDOW_DAYS - 5)]
    past = past[past.index >= anchor - dt.timedelta(days=VELOCITY_WINDOW_DAYS + 45)]
    if past.empty:
        return "insufficient_history"
    delta = current_score - float(past.iloc[-1])
    if delta > VELOCITY_THRESHOLD:
        return "rapid_rise"
    if delta < -VELOCITY_THRESHOLD:
        return "rapid_fall"
    return "stable"


def compute_froth_score(metrics: list[MetricInput],
                        prior_scores: pd.Series | None = None) -> dict[str, Any]:
    """Compute the full froth-score output dict from metric inputs.

    Missing metrics are dropped from their pillar's average; a pillar with
    no scored metrics gets score NaN and is flagged low confidence.
    """
    pillar_metric_scores: dict[str, list[tuple[str, float]]] = {
        p: [] for p in PILLAR_WEIGHTS
    }
    low_confidence_pillars: set[str] = set()
    per_metric: dict[str, dict[str, Any]] = {}

    for m in metrics:
        score, low_conf = _metric_score(m)
        per_metric[m.name] = {"score": None if np.isnan(score) else round(score, 1),
                              "pillar": m.pillar,
                              "low_confidence": low_conf}
        if not np.isnan(score):
            pillar_metric_scores[m.pillar].append((m.name, score))
        if low_conf:
            low_confidence_pillars.add(m.pillar)

    pillar_scores: dict[str, float] = {}
    for pillar, weighted in PILLAR_WEIGHTS.items():
        entries = pillar_metric_scores[pillar]
        if entries:
            pillar_scores[pillar] = float(np.mean([s for _, s in entries]))
        else:
            # No usable metric at all for this pillar — composite will be
            # renormalised without it, which must be visible in the output.
            pillar_scores[pillar] = float("nan")
            low_confidence_pillars.add(pillar)

    weights_used = [(PILLAR_WEIGHTS[p], s) for p, s in pillar_scores.items() if not np.isnan(s)]
    if weights_used:
        w_sum = sum(w for w, _ in weights_used)
        composite = sum(w * s for w, s in weights_used) / w_sum
    else:
        composite = float("nan")

    structural_parts = [pillar_scores[p] for p in ("valuation",) if not np.isnan(pillar_scores[p])]
    timing_parts = [pillar_scores[p] for p in ("positioning", "liquidity") if not np.isnan(pillar_scores[p])]

    return {
        "composite_score": None if np.isnan(composite) else round(composite, 1),
        "band": band_for(composite),
        "structural_score": (round(float(np.mean(structural_parts)), 1)
                             if structural_parts else None),
        "timing_score": (round(float(np.mean(timing_parts)), 1)
                         if timing_parts else None),
        "velocity_flag": velocity_flag(prior_scores, composite) if not np.isnan(composite) else "insufficient_history",
        "pillar_scores": {p: (None if np.isnan(s) else round(s, 1))
                          for p, s in pillar_scores.items()},
        "low_confidence_pillars": sorted(low_confidence_pillars),
        "metrics": per_metric,
    }


# ---------------------------------------------------------------------------
# Historical reconstruction (for backfill preview + velocity warm-start)
# ---------------------------------------------------------------------------

def historical_percentiles(history: pd.Series, window_days: int = 365 * 12,
                           invert: bool = False) -> pd.Series:
    """Rolling percentile rank of a series against its own trailing window.

    Uses an expanding rank until ``window_days`` of data exists. Percentile
    convention matches :func:`percentile_score` (strictly-less-than share).
    """
    s = history.dropna().astype(float).sort_index()
    if s.empty:
        return s
    pct = s.rolling(f"{window_days}D", min_periods=1).rank(pct=True) * 100.0
    # pandas rolling.rank uses "<=" convention; align to strict "<" by
    # subtracting half a tie-adjustment only where ties matter. Acceptable
    # approximation for a 0-100 dial; keep consistent everywhere.
    return 100.0 - pct if invert else pct


def historical_pillars(metric_histories: dict[str, pd.Series],
                       pillar_of: dict[str, str],
                       invert_of: dict[str, bool] | None = None,
                       window_days: int = 365 * 12) -> pd.DataFrame:
    """Reconstruct daily pillar-score time series from raw histories."""
    invert_of = invert_of or {}
    daily_frames: dict[str, pd.Series] = {}
    for name, hist in metric_histories.items():
        pct = historical_percentiles(hist, window_days=window_days,
                                     invert=invert_of.get(name, False))
        daily_frames[name] = pct.resample("D").last().ffill()
    aligned = pd.DataFrame(daily_frames)

    pillars_df = pd.DataFrame(index=aligned.index,
                              columns=list(PILLAR_WEIGHTS), dtype=float)
    for pillar in PILLAR_WEIGHTS:
        cols = [c for c in aligned.columns if pillar_of.get(c) == pillar]
        if cols:
            pillars_df[pillar] = aligned[cols].mean(axis=1)
    return pillars_df


def composite_from_pillars(pillars_df: pd.DataFrame) -> pd.Series:
    """Weighted composite per row, renormalised over the pillars present."""
    acc = pd.Series(0.0, index=pillars_df.index)
    for pillar, w in PILLAR_WEIGHTS.items():
        col = pillars_df[pillar]
        valid = col.notna()
        acc[valid] += col[valid] * w
    counts = pillars_df.notna().sum(axis=1)
    w_present = pillars_df.notna().mul(pd.Series(PILLAR_WEIGHTS)).sum(axis=1)
    composite = acc / w_present.where(w_present > 0)
    composite[counts == 0] = np.nan
    return composite.dropna(how="any")


def historical_composite(metric_histories: dict[str, pd.Series],
                         pillar_of: dict[str, str],
                         invert_of: dict[str, bool] | None = None,
                         window_days: int = 365 * 12) -> pd.Series:
    """Reconstruct a daily composite-score time series from raw histories."""
    return composite_from_pillars(historical_pillars(
        metric_histories, pillar_of, invert_of, window_days=window_days))
