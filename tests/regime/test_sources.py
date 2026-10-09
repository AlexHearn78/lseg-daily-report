"""Offline tests for regime data sources (no network access)."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from lseg_quant.regime.lseg_feeds import spx_skew
from lseg_quant.regime.sources import lseg


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


def test_parse_pricing_uses_default_field() -> None:
    payload = [{"defaultPricingField": "PUTCAL_RTO",
                "headers": [{"name": "DATE"}, {"name": "HIGH_1"}, {"name": "PUTCAL_RTO"}],
                "data": [["2026-10-08", 89.4, 1.447], ["2026-10-07", 2.6, None],
                         ["2026-10-06", 38.5, 2.141]]}]
    s = lseg.parse_pricing(payload)
    assert list(s.index.strftime("%Y-%m-%d")) == ["2026-10-06", "2026-10-08"]
    assert s.iloc[-1] == 1.447


def test_parse_gvt_maps_terms_to_store_names() -> None:
    payload = {"results": [{"pricingDate": "2026-10-08",
                            "points": {"columns": ["rate", "term"], "rows": [[4.7566, 2], [5.231, 10]]}}]}
    out = lseg.parse_gvt(payload)
    assert out["dgs2"] == {pd.Timestamp("2026-10-08"): 4.7566}
    assert out["dgs10"] == {pd.Timestamp("2026-10-08"): 5.231}
    assert lseg.parse_gvt(payload["results"]) == out  # bare list form


def test_real_policy_rate_uses_published_cpi_only() -> None:
    effr = pd.Series(4.0, index=pd.date_range("2026-01-01", "2026-09-10", freq="D"))
    cpi = pd.Series([3.0, 2.9, 2.8, 2.7, 2.6, 2.5, 2.4],
                    index=pd.date_range("2026-01-01", periods=7, freq="MS"))
    real = lseg.derived_metrics({"effr": effr, "cpi_yoy": cpi})["real_policy_rate"]
    # The July print (dated 1 Jul) only becomes usable 45 days later, on 15 Aug.
    assert real[pd.Timestamp("2026-08-14")] == pytest.approx(4.0 - 2.5)
    assert real[pd.Timestamp("2026-08-15")] == pytest.approx(4.0 - 2.4)
    assert real.index.max() == pd.Timestamp("2026-09-10")


def test_funding_spread_and_stretch() -> None:
    idx = pd.bdate_range(end="2026-10-08", periods=260)
    out = lseg.derived_metrics({
        "sofr": pd.Series(3.87, index=idx), "effr": pd.Series(3.88, index=idx),
        "spx_close": pd.Series([100.0] * 230 + [110.0] * 30, index=idx)})
    assert out["funding_spread"].iloc[-1] == pytest.approx(-0.01)
    stretch = out["spx_stretch"]
    assert stretch.index[0] == idx[199]          # needs 200 days of prices
    avg = (100 * 170 + 110 * 30) / 200
    assert stretch.iloc[-1] == pytest.approx((110 / avg - 1) * 100)


class _FakeLseg:
    """Answers the three LSEG tools refresh() calls, from fixed data."""

    def call_tool(self, name: str, args: dict) -> dict:
        if name == "historical_pricing_summaries":
            days = pd.bdate_range(args["start"], args["end"])
            rows = [[d.strftime("%Y-%m-%d"), 1.0] for d in days]
            body = {"data": [{"defaultPricingField": "FIXING_1",
                              "headers": [{"name": "DATE"}, {"name": "FIXING_1"}], "data": rows}]}
        elif name == "fixed_income_curves":
            body = {"results": [{"pricingDate": c["pricingDate"],
                                 "points": {"columns": ["rate", "term"], "rows": [[4.0, 2], [4.5, 10]]}}
                                for c in args["curves"]]}
        else:  # qa_macroeconomic
            months = pd.date_range("2014-01-01", "2026-08-01", freq="MS")
            rows = [{"period": d.strftime("%Y-%m-%d"), "value": 300 + i} for i, d in enumerate(months)]
            body = [{"response": {"data": rows}}]
        return {"result": {"content": [{"text": json.dumps(body)}]}}


def test_refresh_backfills_every_series(tmp_path) -> None:
    from lseg_quant.regime.history import HistoryStore

    store = HistoryStore(tmp_path)
    errors = lseg.refresh(store, _FakeLseg(), today=dt.date(2026, 10, 8))
    assert errors == {}
    for name in ("spx_close", "sofr", "effr", "eurex_putcall_sx5e", "dgs2", "dgs10",
                 "cpi_yoy", "funding_spread", "real_policy_rate", "spx_stretch"):
        assert len(store.read(name)), name
    assert store.read("spx_close").index.min() <= pd.Timestamp("2016-10-11")
    # A second run fetches only the overlap window.
    assert lseg._start_for(store, "sofr", dt.date(2026, 10, 9)) == dt.date(2026, 9, 28)
