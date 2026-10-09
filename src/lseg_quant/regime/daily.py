"""Daily regime context assembly for the MDU job.

Reads the CSV history store, produces today's froth-score payload (spec
shape) and the market-level risk-off regime, and exposes a loader for the
most recent ``froth_score.json`` written by ``workflows/regime_froth.py``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from lseg_quant.config import settings
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.market_regime import compute_market_regime
from lseg_quant.regime.score import (
    LOW_CONFIDENCE_YEARS,
    MetricInput,
    composite_from_pillars,
    compute_froth_score,
    historical_pillars,
)

logger = logging.getLogger(__name__)

PILLAR_OF: dict[str, str] = {
    "hy_oas": "valuation",
    "margin_debit_balances": "leverage",
    "margin_loans_z1": "leverage",
    "excess_leverage": "leverage",
    "cftc_net_spec": "positioning",
    "lseg_spx_skew": "positioning",
    "cboe_putcall_monthly": "positioning",
    "cboe_putcall": "positioning",
    "ici_equity_flows": "positioning",
    "funding_spread": "liquidity",
    "real_policy_rate": "liquidity",
}
INVERT_OF: dict[str, bool] = {
    "hy_oas": True,
    "cboe_putcall": True,
    "cboe_putcall_monthly": True,
    "lseg_spx_skew": True,
    "funding_spread": True,
    "real_policy_rate": True,
}

OUTPUT_DIRNAME = "regime"

# Max age (days) of a metric's latest observation before it is treated as
# dead and left out of the score. Sized to each source's cadence plus its
# publication lag: Z.1 quarterly data is dated to the quarter start and
# lands ~5 months later.
MAX_STALENESS_DAYS: dict[str, int] = {
    "hy_oas": 14,
    "funding_spread": 14,
    "real_policy_rate": 14,
    "lseg_spx_skew": 14,
    "cboe_putcall": 14,
    "cftc_net_spec": 30,
    "ici_equity_flows": 30,
    "margin_debit_balances": 90,
    "cboe_putcall_monthly": 90,
    "margin_loans_z1": 400,
    "excess_leverage": 400,
}

# How much history froth_score.json keeps for the report charts.
CHART_YEARS = 10
SPX_CHART_YEARS = 2


def load_metric_inputs(store: HistoryStore, as_of: pd.Timestamp | None = None,
                       ) -> tuple[list[MetricInput], pd.Series, dict[str, str], pd.DataFrame]:
    """Build today's metric inputs + historical composite for velocity.

    Metrics whose latest observation is older than ``MAX_STALENESS_DAYS``
    are left out of both and returned as ``{name: last_observation_date}``.
    The fourth element is the daily pillar-score history behind the composite.
    """
    as_of = (as_of or pd.Timestamp.now()).normalize()
    metrics: list[MetricInput] = []
    histories: dict[str, pd.Series] = {}
    stale: dict[str, str] = {}
    for name, pillar in PILLAR_OF.items():
        hist = store.read(name)
        if not len(hist):
            continue
        last = hist.index.max()
        if (as_of - last).days > MAX_STALENESS_DAYS.get(name, 30):
            stale[name] = last.strftime("%Y-%m-%d")
            continue
        histories[name] = hist
        value = float(hist.iloc[-1])
        # The current observation must not rank against itself.
        history_excl = hist.iloc[:-1]
        if not len(history_excl):
            history_excl = hist
        metrics.append(MetricInput(
            name=name, pillar=pillar, value=value,
            history=history_excl, invert=INVERT_OF.get(name, False),
        ))
    pillars = historical_pillars(histories, PILLAR_OF, INVERT_OF)
    prior = composite_from_pillars(pillars)
    return metrics, prior, stale, pillars


def _monthly_points(series: pd.Series, years: int, as_of: pd.Timestamp,
                    digits: int = 2) -> list[list[Any]]:
    """Last observation of each month over the trailing *years*, as [date, value]."""
    s = series.dropna().sort_index()
    if not len(s):
        return []
    s = s[s.index >= as_of - pd.DateOffset(years=years)]
    s = s.groupby(s.index.to_period("M")).tail(1)
    return [[d.strftime("%Y-%m-%d"), round(float(v), digits)] for d, v in s.items()]


def _ending_today(points: list[list[Any]], as_of: pd.Timestamp,
                  today: float | None) -> list[list[Any]]:
    """Replace the current month's point with today's actual score.

    The rebuilt history ranks with pandas' tie convention, so its last point
    can differ slightly from the score the report shows; the chart should end
    on the reported number.
    """
    month = as_of.strftime("%Y-%m")
    kept = [p for p in points if not p[0].startswith(month)]
    if today is not None:
        kept.append([as_of.strftime("%Y-%m-%d"), today])
    return kept


def _metric_detail(m: MetricInput) -> dict[str, Any]:
    """Raw value, dates and scoring method for one metric (report drilldown)."""
    hist = m.history.dropna()
    span_years = ((hist.index.max() - hist.index.min()).days / 365.25
                  if len(hist) >= 2 else 0.0)
    method = ("percentile" if span_years >= LOW_CONFIDENCE_YEARS and len(hist) >= 5
              else "min-max")
    return {
        "value": None if m.value is None else round(float(m.value), 4),
        "history_start": hist.index.min().strftime("%Y-%m-%d") if len(hist) else None,
        "n_obs": int(len(hist)),
        "inverted": m.invert,
        "method": method,
        "window_years": round(m.window_days / 365, 1),
    }


def compute_froth_payload(as_of: pd.Timestamp | None = None,
                          store: HistoryStore | None = None) -> dict[str, Any]:
    """Full froth-score output dict for today (or ``as_of``)."""
    store = store or HistoryStore()
    as_of = (as_of or pd.Timestamp.now()).normalize()
    metrics, prior, stale, pillars = load_metric_inputs(store, as_of)
    if stale:
        logger.warning("regime: excluding stale metrics %s", stale)
    result = compute_froth_score(metrics, prior_scores=prior)
    result["stale_metrics"] = stale
    result["as_of"] = as_of.strftime("%Y-%m-%d")

    for m in metrics:
        detail = _metric_detail(m)
        detail["last_date"] = store.read(m.name).dropna().index.max().strftime("%Y-%m-%d")
        result["metrics"][m.name].update(detail)

    # Trailing series for the report charts; consumers that only need
    # today's numbers (feature layer, briefing pack) ignore this block.
    # Charts carry each pillar forward between releases (a quarterly series
    # still counts until its next print, as it does in today's score).
    pillars_ff = pillars.ffill()
    history: dict[str, Any] = {
        "composite": _ending_today(
            _monthly_points(composite_from_pillars(pillars_ff), CHART_YEARS, as_of, 1),
            as_of, result["composite_score"]),
        "pillars": {p: _ending_today(_monthly_points(pillars_ff[p], CHART_YEARS, as_of, 1),
                                     as_of, result["pillar_scores"].get(p))
                    for p in pillars_ff.columns},
        "structural": _ending_today(
            _monthly_points(pillars_ff[["valuation", "leverage"]].mean(axis=1),
                            CHART_YEARS, as_of, 1), as_of, result["structural_score"]),
        "timing": _ending_today(
            _monthly_points(pillars_ff[["positioning", "liquidity"]].mean(axis=1),
                            CHART_YEARS, as_of, 1), as_of, result["timing_score"]),
        "metrics": {m.name: _monthly_points(store.read(m.name), CHART_YEARS, as_of, 4)
                    for m in metrics},
    }
    spx = store.read("spx_close").dropna()
    if len(spx):
        spx = spx[spx.index >= as_of - pd.DateOffset(years=SPX_CHART_YEARS)]
        history["spx_close"] = [[d.strftime("%Y-%m-%d"), round(float(v), 2)]
                                for d, v in spx.resample("W-FRI").last().dropna().items()]
    dgs2, dgs10 = store.read("dgs2"), store.read("dgs10")
    if len(dgs2) and len(dgs10):
        curve = (dgs10 - dgs2).dropna()
        history["spread_2s10s"] = _monthly_points(curve, CHART_YEARS, as_of, 2)
    result["history"] = history
    return result


def compute_regime_payload(store: HistoryStore | None = None) -> dict[str, Any]:
    """Market-wide regime classification from stored series."""
    store = store or HistoryStore()
    spx = store.read("spx_close")
    funding = store.read("funding_spread")
    dgs2 = store.read("dgs2")
    dgs10 = store.read("dgs10")

    spread_2s10s = None
    if len(dgs2) and len(dgs10):
        joined = pd.concat([dgs2.rename("dgs2"), dgs10.rename("dgs10")], axis=1).dropna()
        if len(joined):
            spread_2s10s = round(float(joined["dgs10"].iloc[-1] - joined["dgs2"].iloc[-1]), 4)

    regime = compute_market_regime(spx, spread_2s10s=spread_2s10s,
                                   funding_spread=funding if len(funding) else None)
    return regime.as_dict()


def latest_output_dir(output_root: Path | None = None,
                      as_of: str | None = None) -> Path | None:
    """Newest dated regime output dir, optionally the newest on/before *as_of*."""
    root = (output_root or settings.output_root) / OUTPUT_DIRNAME
    if not root.is_dir():
        return None
    dated = sorted(d for d in root.iterdir() if d.is_dir())
    if as_of:
        dated = [d for d in dated if d.name <= as_of]
    return dated[-1] if dated else None


def load_latest_context(max_age_days: int = 4,
                        as_of: str | None = None) -> dict[str, Any] | None:
    """Newest froth_score.json contents, or None when stale/absent.

    ``max_age_days`` guards against weekend/holiday staleness being mistaken
    for live data during the ticker batch. ``as_of`` (YYYY-MM-DD) picks the
    newest file on or before that date, for re-rendering a past report.
    """
    out_dir = latest_output_dir(as_of=as_of) if as_of else latest_output_dir()
    if out_dir is None:
        logger.info("regime: no froth-score outputs yet")
        return None
    path = out_dir / "froth_score.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("regime: unreadable %s: %s", path, exc)
        return None
    anchor = pd.Timestamp(as_of) if as_of else pd.Timestamp.now().normalize()
    age = (anchor - pd.Timestamp(payload.get("as_of"))).days
    if not pd.isna(age) and age > max_age_days:
        logger.info("regime: froth score from %s is %d days old — ignoring",
                    payload.get("as_of"), age)
        return None
    return payload


def market_context_for_features(context: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a froth_score.json payload to the feature-layer contract."""
    if not context:
        return None
    regime_block = context.get("market_regime") or {}
    return {
        "regime": regime_block.get("regime", "neutral"),
        "froth_composite": context.get("composite_score"),
        "froth_band": context.get("band"),
        "froth_velocity": context.get("velocity_flag"),
    }


def prompt_block(context: dict[str, Any] | None) -> str:
    """Markdown section describing the market regime for the LLM prompt."""
    if not context:
        return ""
    regime = (context.get("market_regime") or {}).get("regime", "unknown")
    reasons = (context.get("market_regime") or {}).get("reasons") or []
    lines = [
        "",
        "---",
        "## Market Regime Context (computed independently of this ticker)",
        f"- Market-wide regime: **{regime}**",
    ]
    for reason in reasons[:3]:
        lines.append(f"  - {reason}")
    composite = context.get("composite_score")
    if composite is not None:
        lines.append(
            f"- Market froth score: **{composite}/100 ({context.get('band')})**, "
            f"velocity: {context.get('velocity_flag')}")
        pillars = context.get("pillar_scores") or {}
        pillar_txt = ", ".join(
            f"{p} {v}" for p, v in pillars.items() if v is not None)
        if pillar_txt:
            lines.append(f"  - Pillars: {pillar_txt}")
        low_conf = context.get("low_confidence_pillars") or []
        if low_conf:
            lines.append(f"  - Low-confidence pillars (thin history): "
                         f"{', '.join(low_conf)}")
    lines.append("Factor this market-level context into your conviction; do not "
                 "restate ticker-level trend evidence as market evidence.")
    return "\n".join(lines) + "\n"
