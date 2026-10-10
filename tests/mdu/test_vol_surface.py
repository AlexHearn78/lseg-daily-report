"""equity_vol_surface request shape, units and tenor labelling."""
from __future__ import annotations

import datetime as dt
import json

import pandas as pd
import pytest

from lseg_quant.mdu.config import TickerInfo
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.mdu.research import ResearchContext

# Shape and units as the live tool returned them for BARC.L on 9 Oct 2026:
# moneyness as a fraction, vols in percent, no weekly expiry.
SURFACE = [[None, "0.9", "1", "1.1"],
           ["2026-11-20", 45.0, 40.0, 37.0],
           ["2026-12-18", 44.0, 39.0, 36.0],
           ["2027-01-15", 43.0, 38.0, 35.0]]


class _Client(MCPClient):
    def __init__(self) -> None:  # no network
        self.seen: dict = {}

    def call_tool(self, name: str, arguments: dict | None = None) -> dict:
        self.seen = arguments or {}
        return {"result": {"content": [{"text": json.dumps([{"surface": SURFACE}])}]}}


def test_request_uses_instrument_and_converts_units() -> None:
    client = _Client()
    df = client.get_equity_vol_surface("BARC.L", calculation_date="2026-10-09")
    assert client.seen["instrument"] == "BARC.L@RIC"
    assert client.seen["surfaces"]["dates"] == ["2026-10-09"]
    assert "instrumentCode" not in client.seen
    assert list(df.columns) == [90.0, 100.0, 110.0]
    assert df.loc["2026-11-20", 100.0] == pytest.approx(0.40)


def test_one_month_atm_uses_the_nearest_expiry() -> None:
    surface = _Client().get_equity_vol_surface("BARC.L", calculation_date="2026-10-09")
    ctx = ResearchContext(ticker="BARC", prices=pd.DataFrame(), yield_curve=pd.DataFrame(),
                          vol_surface=surface, news_headlines=[], ibes_consensus=[],
                          fundamentals=[], as_of=dt.datetime(2026, 10, 9))
    vol = ctx._module_vol()
    # 20 Nov is 42 days out, the closest to one month; 18 Dec is 70 days.
    assert vol["atm_iv_1m_pct"] == pytest.approx(40.0)


def test_ibes_id_override() -> None:
    assert TickerInfo(ric="LIN.O", ticker_display="LIN").ibes_id == ("LIN.O", "RIC")
    assert TickerInfo(ric="LIN.O", ticker_display="LIN", ibes_ticker="LIN").ibes_id == ("LIN", "Ticker")
