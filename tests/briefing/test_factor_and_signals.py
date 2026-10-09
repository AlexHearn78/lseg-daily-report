"""Main-factor attribution and the shadow earnings card (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from lseg_quant.briefing.pack import TickerInput, build_pack, factor_attribution
from lseg_quant.briefing.render import briefing_email_rows, template_sections


class _EmptyStore:
    def read(self, name: str) -> pd.Series:
        return pd.Series(dtype=float)


def _closes(jump: float) -> pd.Series:
    rets = np.random.default_rng(1).normal(0, 0.01, 40)
    rets[-1] = jump
    return pd.Series(100 * np.cumprod(1 + rets), index=pd.bdate_range(end="2026-09-14", periods=40))


def test_factor_attribution_picks_the_largest_weighted_push() -> None:
    # pushes: valuation 0.30*0.2=.06, trend 0.20*-0.5=-.10, macro .15*.4=.06, vol .10*.3=.03
    fa = factor_attribution({"valuation": 0.2, "trend": -0.5, "risk": 0.0,
                             "macro": 0.4, "news": 0.0, "vol": 0.3})
    assert (fa["factor"], fa["direction"], fa["share_pct"]) == ("trend", "down", 40)
    assert fa["contribution"] == -0.1
    assert factor_attribution({}) is None
    assert factor_attribution({"valuation": 0.0}) is None


def test_report_names_main_factor_and_shows_shadow_signal() -> None:
    tickers = [TickerInput(key="VOW3", composite=0.24, closes=_closes(0.05),
                           dimension_scores={"valuation": 0.8, "trend": 0.1, "macro": 0.0})]
    pack = build_pack("2026-09-14", tickers, froth=None, store=_EmptyStore())
    pack["earnings_signals"] = [{
        "key": "VOW3", "trading_days_since": 3, "score": 0.5, "weight": 0.19,
        "surprise": {"EPS": {"pct": 8.0, "vintage": "latest_published"}},
        "revisions": {}, "composite": 0.24, "shadow_composite": 0.29,
        "action": "HOLD", "shadow_action": "BUY",
    }]
    rows = briefing_email_rows(template_sections(pack), pack)
    assert "Main factor" in rows and "Valuation ▲ 92%" in rows
    assert "Earnings signal (shadow)" in rows
    assert "+0.24 → +0.29 BUY" in rows and "+8.0%*" in rows
