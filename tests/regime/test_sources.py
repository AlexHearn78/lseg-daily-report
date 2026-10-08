"""Offline tests for regime data sources (no network access)."""
from __future__ import annotations

import json

import pandas as pd
import pytest

from lseg_quant.regime.lseg_feeds import spx_skew
from lseg_quant.regime.sources import fred


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._payload


def test_fetch_series_sends_series_specific_params(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict = {}

    def fake_get(url, params, timeout):
        seen.update(params)
        return _FakeResponse({"observations": [{"date": "2026-07-01", "value": "2.9"}]})

    monkeypatch.setattr(fred.requests, "get", fake_get)
    fred.fetch_series("cpi_yoy", api_key_value="k")
    # Without units=pc1 FRED returns the CPI index level (~330), not YoY %.
    assert seen["units"] == "pc1"


def test_real_policy_rate_uses_published_cpi_only() -> None:
    ff = pd.Series(4.0, index=pd.date_range("2026-01-01", "2026-09-10", freq="D"))
    cpi = pd.Series([3.0, 2.9, 2.8, 2.7, 2.6, 2.5, 2.4],
                    index=pd.date_range("2026-01-01", periods=7, freq="MS"))
    real = fred.derived_metrics({"fed_funds_upper": ff, "cpi_yoy": cpi})["real_policy_rate"]
    # The July print (dated 1 Jul) only becomes usable 45 days later, on 15 Aug.
    assert real[pd.Timestamp("2026-08-14")] == pytest.approx(4.0 - 2.5)
    assert real[pd.Timestamp("2026-08-15")] == pytest.approx(4.0 - 2.4)
    # The last print carries forward to the latest policy-rate date.
    assert real.index.max() == pd.Timestamp("2026-09-10")


class _FakeMCP:
    def __init__(self, grid: list) -> None:
        self._grid = grid

    def call_tool(self, name: str, args: dict) -> dict:
        text = json.dumps([{"surface": self._grid}])
        return {"result": {"content": [{"text": text}]}}


def test_spx_skew_reads_90_and_100_moneyness() -> None:
    grid = [[None, "0.9", "0.95", "1", "1.025"],
            ["2026-12-01", 20.0, 16.0, 15.0, 13.0]]
    # IV(0.90) - IV(1.00) = 20 - 15; the old off-by-one read 16 - 13.
    assert spx_skew(_FakeMCP(grid), "2026-09-01") == pytest.approx(5.0)
