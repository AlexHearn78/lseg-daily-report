"""Tests for the regime→features/overrides integration (Phase 3)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lseg_quant.mdu.decision import LLMDecision
from lseg_quant.mdu.features import compute_features
from lseg_quant.mdu.overrides import RiskOverrideConfig, RiskOverrideEngine
from lseg_quant.regime.daily import (
    compute_froth_payload,
    market_context_for_features,
    prompt_block,
)


# ---------------------------------------------------------------------------
# Feature-layer contract
# ---------------------------------------------------------------------------

def _prices(n: int = 300, drift: float = 0.0005) -> pd.DataFrame:
    idx = pd.bdate_range(end="2026-08-21", periods=n)
    close = 100.0 * (1.0 + drift) ** pd.RangeIndex(n)
    return pd.DataFrame({"close": close}, index=idx)


def test_market_context_overrides_macro_regime():
    ctx = {"regime": "risk_off", "froth_composite": 55.0,
           "froth_band": "balanced", "froth_velocity": "stable"}
    feats = compute_features(_prices(drift=0.002), market_context=ctx)
    assert feats["macro_regime"] == "risk_off"
    assert feats["froth_composite"] == 55.0
    assert feats["froth_band"] == "balanced"


def test_no_market_context_keeps_legacy_behaviour():
    feats = compute_features(_prices(drift=0.002))  # uptrend -> risk_on proxy
    assert feats["macro_regime"] == "risk_on"
    assert "froth_composite" not in feats


def test_market_context_applies_to_default_vector():
    ctx = {"regime": "risk_off"}
    feats = compute_features(pd.DataFrame(), market_context=ctx)
    assert feats["macro_regime"] == "risk_off"


# ---------------------------------------------------------------------------
# Gate 5 tiered caps
# ---------------------------------------------------------------------------

def _decision(allocation: float = 0.50) -> LLMDecision:
    return LLMDecision(action="BUY", confidence=0.9, allocation=allocation,
                       reasoning=["ok"], risk_flags=[])


def test_gate5_extreme_froth_caps_regardless_of_regime():
    eng = RiskOverrideEngine(RiskOverrideConfig())
    out = eng.apply(_decision(), features={"macro_regime": "neutral",
                                           "froth_composite": 85.0})
    assert out.allocation <= RiskOverrideConfig().froth_extreme_cap
    assert "froth_extreme_cap" in out.risk_flags


def test_gate5_risk_off_with_low_froth_lifts_cap():
    eng = RiskOverrideEngine(RiskOverrideConfig())
    out = eng.apply(_decision(0.50), features={"macro_regime": "risk_off",
                                               "froth_composite": 10.0})
    assert out.allocation <= RiskOverrideConfig().froth_calm_cap
    assert out.allocation > RiskOverrideConfig().risk_off_cap


def test_gate5_risk_off_mid_froth_keeps_default_cap():
    eng = RiskOverrideEngine(RiskOverrideConfig())
    out = eng.apply(_decision(0.50), features={"macro_regime": "risk_off",
                                               "froth_composite": 55.0})
    assert out.allocation <= RiskOverrideConfig().risk_off_cap


def test_gate5_without_froth_matches_legacy_behaviour():
    eng = RiskOverrideEngine(RiskOverrideConfig())
    out = eng.apply(_decision(0.50), features={"macro_regime": "risk_off"})
    assert out.allocation <= RiskOverrideConfig().risk_off_cap
    neutral = eng.apply(_decision(0.50), features={"macro_regime": "neutral"})
    assert neutral.allocation == 0.50


# ---------------------------------------------------------------------------
# Payload plumbing
# ---------------------------------------------------------------------------

def test_prompt_block_renders_score_and_pillars():
    block = prompt_block({
        "composite_score": 61.6, "band": "elevated",
        "velocity_flag": "rapid_rise",
        "pillar_scores": {"valuation": 70.1, "liquidity": None},
        "low_confidence_pillars": ["valuation"],
        "market_regime": {"regime": "risk_on", "reasons": ["12m +8%"]},
    })
    assert "61.6/100 (elevated)" in block
    assert "rapid_rise" in block
    assert "valuation 70.1" in block
    assert "risk_on" in block


def test_prompt_block_empty_when_no_context():
    assert prompt_block(None) == ""


def test_stale_metric_excluded_from_score(tmp_path: Path):
    from lseg_quant.regime.history import HistoryStore

    store = HistoryStore(tmp_path)
    idx = pd.bdate_range(end="2026-09-10", periods=1000)
    store.upsert("hy_oas", pd.Series(range(1000), index=idx, dtype=float))
    old = pd.date_range("2019-01-31", periods=8, freq="ME")
    store.upsert("cboe_putcall_monthly", pd.Series(range(8), index=old, dtype=float))

    payload = compute_froth_payload(as_of=pd.Timestamp("2026-09-11"), store=store)
    assert payload["stale_metrics"] == {"cboe_putcall_monthly": "2019-08-31"}
    assert "cboe_putcall_monthly" not in payload["metrics"]
    assert "hy_oas" in payload["metrics"]


def test_payload_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    as_of = pd.Timestamp.now().strftime("%Y-%m-%d")  # fresh, so not rejected as stale
    payload = {
        "as_of": as_of, "composite_score": 57.8, "band": "balanced",
        "velocity_flag": "stable", "market_regime": {"regime": "risk_on"},
    }
    out_dir = tmp_path / "regime" / as_of
    out_dir.mkdir(parents=True)
    (out_dir / "froth_score.json").write_text(json.dumps(payload))

    import lseg_quant.regime.daily as daily
    monkeypatch.setattr(daily, "latest_output_dir", lambda root=None: out_dir)
    ctx = daily.load_latest_context()
    reduced = market_context_for_features(ctx)
    assert reduced["regime"] == "risk_on"
    assert reduced["froth_composite"] == 57.8
