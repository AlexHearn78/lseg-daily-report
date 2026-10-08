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
    MetricInput,
    compute_froth_score,
    historical_composite,
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


def load_metric_inputs(store: HistoryStore, as_of: pd.Timestamp | None = None,
                       ) -> tuple[list[MetricInput], pd.Series, dict[str, str]]:
    """Build today's metric inputs + historical composite for velocity.

    Metrics whose latest observation is older than ``MAX_STALENESS_DAYS``
    are left out of both and returned as ``{name: last_observation_date}``.
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
    prior = historical_composite(histories, PILLAR_OF, INVERT_OF)
    return metrics, prior, stale


def compute_froth_payload(as_of: pd.Timestamp | None = None,
                          store: HistoryStore | None = None) -> dict[str, Any]:
    """Full froth-score output dict for today (or ``as_of``)."""
    store = store or HistoryStore()
    as_of = (as_of or pd.Timestamp.now()).normalize()
    metrics, prior, stale = load_metric_inputs(store, as_of)
    if stale:
        logger.warning("regime: excluding stale metrics %s", stale)
    result = compute_froth_score(metrics, prior_scores=prior)
    result["stale_metrics"] = stale
    result["as_of"] = as_of.strftime("%Y-%m-%d")
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


def latest_output_dir(output_root: Path | None = None) -> Path | None:
    root = (output_root or settings.output_root) / OUTPUT_DIRNAME
    if not root.is_dir():
        return None
    dated = sorted(d for d in root.iterdir() if d.is_dir())
    return dated[-1] if dated else None


def load_latest_context(max_age_days: int = 4) -> dict[str, Any] | None:
    """Newest froth_score.json contents, or None when stale/absent.

    ``max_age_days`` guards against weekend/holiday staleness being mistaken
    for live data during the ticker batch.
    """
    out_dir = latest_output_dir()
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
    age = (pd.Timestamp.now().normalize() - pd.Timestamp(payload.get("as_of"))).days
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
