"""Offline unit tests for the froth score math (no network access)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from lseg_quant.regime.score import (
    MetricInput,
    band_for,
    compute_froth_score,
    historical_percentiles,
    minmax_score,
    percentile_score,
    velocity_flag,
)


def _hist(days: int = 400, seed: int = 7, end: str = "2026-08-21") -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=end, periods=days)
    return pd.Series(rng.normal(50, 10, days).cumsum(), index=idx)


class TestPercentileScore:
    def test_high_value_scores_high(self) -> None:
        hist = _hist()
        assert percentile_score(float(hist.max()) + 1, hist) == 100.0

    def test_low_value_scores_low(self) -> None:
        hist = _hist()
        assert percentile_score(float(hist.min()) - 1, hist) == 0.0

    def test_median_around_middle(self) -> None:
        hist = _hist()
        score = percentile_score(float(hist.median()), hist)
        assert 40.0 <= score <= 60.0

    def test_invert_flips(self) -> None:
        hist = _hist()
        v = float(hist.max()) + 1
        assert percentile_score(v, hist, invert=True) == 0.0
        assert percentile_score(v, hist) == 100.0

    def test_empty_history_nan(self) -> None:
        empty = pd.Series(dtype=float)
        assert np.isnan(percentile_score(1.0, empty))


class TestMinmaxScore:
    def test_bounds(self) -> None:
        hist = _hist()
        assert minmax_score(float(hist.min()), hist) == 0.0
        assert minmax_score(float(hist.max()), hist) == 100.0

    def test_degenerate_range_returns_neutral(self) -> None:
        flat = pd.Series([5.0, 5.0, 5.0])
        assert minmax_score(5.0, flat) == 50.0


class TestBandsAndVelocity:
    def test_band_boundaries(self) -> None:
        assert band_for(10) == "capitulation"
        assert band_for(30) == "risk_off"
        assert band_for(50) == "balanced"
        assert band_for(70) == "elevated"
        assert band_for(95) == "frothy"

    def test_velocity_rapid_rise(self) -> None:
        idx = pd.bdate_range(end="2026-08-21", periods=100)
        scores = pd.Series(np.linspace(30, 70, 100), index=idx)
        assert velocity_flag(scores, 70.0) == "rapid_rise"

    def test_velocity_rapid_fall(self) -> None:
        idx = pd.bdate_range(end="2026-08-21", periods=100)
        scores = pd.Series(np.linspace(70, 30, 100), index=idx)
        assert velocity_flag(scores, 30.0) == "rapid_fall"

    def test_velocity_stable(self) -> None:
        idx = pd.bdate_range(end="2026-08-21", periods=200)
        scores = pd.Series(55 + np.sin(np.arange(200)) * 2, index=idx)
        assert velocity_flag(scores, 56.0) == "stable"

    def test_velocity_insufficient_history(self) -> None:
        scores = pd.Series([50.0])
        assert velocity_flag(scores, 50.0) == "insufficient_history"


class TestComputeFrothScore:
    def _metric(self, name: str, pillar: str, value: float, invert: bool = False) -> MetricInput:
        return MetricInput(name=name, pillar=pillar, value=value,
                           history=_hist(seed=hash(name) % 1000), invert=invert)

    def test_full_composite_shape(self) -> None:
        metrics = [
            self._metric("spx_stretch", "valuation", 60.0),
            self._metric("eurex_putcall_sx5e", "positioning", 60.0, invert=True),
            self._metric("lseg_spx_skew", "positioning", 60.0, invert=True),
            self._metric("funding_spread", "liquidity", 60.0, invert=True),
        ]
        out = compute_froth_score(metrics)
        assert set(out["pillar_scores"]) == {"valuation", "positioning", "liquidity"}
        assert out["structural_score"] is not None
        assert out["timing_score"] is not None
        assert 0 <= out["composite_score"] <= 100
        assert out["band"] in {"capitulation", "risk_off", "balanced", "elevated", "frothy"}

    def test_missing_pillar_renormalises_weights(self) -> None:
        metrics = [self._metric("lseg_spx_skew", "positioning", 90.0)]
        out = compute_froth_score(metrics)
        # Only positioning present -> composite equals the positioning pillar score.
        assert out["composite_score"] == pytest.approx(out["pillar_scores"]["positioning"])
        assert set(out["low_confidence_pillars"]) >= {"valuation", "liquidity"}

    def test_short_history_flags_low_confidence(self) -> None:
        short = _hist(days=60)
        metric = MetricInput(name="thin", pillar="positioning",
                             value=float(short.iloc[-1]), history=short)
        out = compute_froth_score([metric])
        assert "positioning" in out["low_confidence_pillars"]

    def test_no_data_yields_unknown_band(self) -> None:
        out = compute_froth_score([])
        assert out["composite_score"] is None
        assert out["band"] == "unknown"


class TestHistoricalPercentiles:
    def test_monotonic_series_endpoints(self) -> None:
        idx = pd.bdate_range(end="2026-08-21", periods=300)
        s = pd.Series(np.arange(300, dtype=float), index=idx)
        pct = historical_percentiles(s)
        # Strictly increasing series: latest obs is the max.
        assert pct.iloc[-1] == pytest.approx(100.0)

    def test_invert_flips(self) -> None:
        idx = pd.bdate_range(end="2026-08-21", periods=300)
        s = pd.Series(np.arange(300, dtype=float), index=idx)
        assert historical_percentiles(s, invert=True).iloc[-1] == pytest.approx(0.0)
