"""Offline tests for the briefing pack and its rendering (no network)."""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
import pytest

from lseg_quant.briefing.pack import (
    EARNINGS_BOOST,
    TickerInput,
    build_pack,
    closest_to_changing,
    composite_from_hold_confidence,
    find_recent_earnings,
    price_moves,
    select_top_stories,
)
from lseg_quant.briefing.render import (
    briefing_email_rows,
    briefing_page_html,
    template_sections,
    validate_sections,
)
from lseg_quant.mdu.scoring import composite_to_decision


def _closes(last_jump: float = 0.0, n: int = 40, seed: int = 3) -> pd.Series:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, 0.01, n)
    rets[-1] = last_jump
    idx = pd.bdate_range(end="2026-09-11", periods=n)
    return pd.Series(100 * np.cumprod(1 + rets), index=idx)


class _EmptyStore:
    def read(self, name: str) -> pd.Series:
        return pd.Series(dtype=float)


def test_price_moves_flags_unusual_day() -> None:
    moves = price_moves(_closes(last_jump=0.05))
    assert moves["ret_1d_pct"] == pytest.approx(5.0, abs=0.01)
    assert moves["move_vs_normal"] > 3
    assert moves["as_of"] == "2026-09-11"


def test_price_moves_short_series_is_all_null() -> None:
    moves = price_moves(pd.Series([100.0]))
    assert all(v is None for v in moves.values())


def test_top_stories_prefer_recent_earnings() -> None:
    entries = [
        {"key": "A", "failed": False, "story_score": 2.5, "moves": {"ret_1d_pct": 3.0}},
        {"key": "B", "failed": False, "story_score": 0.4 + EARNINGS_BOOST, "moves": {"ret_1d_pct": 0.5}},
        {"key": "C", "failed": True, "story_score": 9.0, "moves": {"ret_1d_pct": 9.0}},
        {"key": "D", "failed": False, "story_score": 1.0, "moves": {"ret_1d_pct": 1.0}},
    ]
    assert select_top_stories(entries, n=2) == ["B", "A"]


def test_closest_to_changing_orders_by_gap() -> None:
    entries = [
        {"key": "A", "name": "A", "action": "HOLD", "composite": 0.20, "drivers": []},
        {"key": "B", "name": "B", "action": "HOLD", "composite": -0.22, "drivers": []},
        {"key": "C", "name": "C", "action": "HOLD", "composite": 0.0, "drivers": []},
        {"key": "D", "name": "D", "action": "BUY", "composite": 0.30, "drivers": []},
    ]
    out = closest_to_changing(entries, n=2)
    assert [o["key"] for o in out] == ["B", "A"]
    assert out[0]["nearest_signal"] == "SELL"
    assert out[1]["nearest_signal"] == "BUY"


@pytest.mark.parametrize("composite", [-0.2, -0.05, 0.0, 0.12, 0.2])
def test_composite_recovered_from_hold_confidence(composite: float) -> None:
    action, confidence, _ = composite_to_decision(composite)
    assert action == "HOLD"
    assert composite_from_hold_confidence(confidence) == pytest.approx(composite, abs=0.006)


class _FakeTranscripts:
    def call_tool(self, name: str, args: dict) -> dict:
        data = []
        for req in args["requests"]:
            pid = req["options"]["oa_permids"][0]
            results = []
            if pid == 4295914405:  # NVDA reported; everyone else did not
                results = [
                    {"chunk_id": "1", "perm_id": 9, "text": "Guidance raised.", "ranking_score": 2.0,
                     "title": "Q2 NVIDIA Earnings Call", "publication_date": "2026-09-09"},
                    {"chunk_id": "2", "perm_id": 9, "text": "Margins lower.", "ranking_score": 1.0,
                     "title": "Q2 NVIDIA Earnings Call", "publication_date": "2026-09-09"},
                ]
            data.append({"options": req["options"], "response": {"results": results}})
        return {"result": {"content": [{"text": json.dumps({"data": data})}]}}


def test_find_recent_earnings_maps_results_back_to_tickers() -> None:
    out = find_recent_earnings(_FakeTranscripts(), ["MSFT", "NVDA", "TSLA"], dt.date(2026, 9, 11))
    assert list(out) == ["NVDA"]
    assert out["NVDA"]["date"] == "2026-09-09"
    assert out["NVDA"]["excerpts"] == ["Guidance raised.", "Margins lower."]


def _pack() -> dict:
    tickers = [
        TickerInput(key="NVDA", composite=0.2, closes=_closes(0.04),
                    headlines=[{"date": "2026-09-11", "headline": "Chip stocks rally"}],
                    drivers=["Trend: +4% 1d"]),
        TickerInput(key="MSFT", composite=-0.1, closes=_closes(0.001, seed=5)),
        TickerInput(key="TSLA", failed=True),
    ]
    return build_pack("2026-09-11", tickers, froth=None, store=_EmptyStore())


def test_build_pack_offline_shape() -> None:
    pack = _pack()
    assert pack["top_stories"][0]["key"] == "NVDA"
    assert pack["summary"] == {"n_tickers": 3, "n_buy": 0, "n_hold": 2, "n_sell": 0, "n_failed": 1}
    assert [c["key"] for c in pack["closest_to_changing"]] == ["NVDA", "MSFT"]
    json.dumps(pack)  # must be serialisable as-is


def test_validate_sections_requires_macro_and_known_tickers() -> None:
    pack = _pack()
    assert validate_sections({"top_stories": []}, pack) is None
    raw = {"market_macro": "Para one.\n\nPara two.",
           "top_stories": [{"ticker": "nvda", "title": "t", "body": "b"},
                           {"ticker": "ZZZZ", "title": "t", "body": "b"}]}
    cleaned = validate_sections(raw, pack)
    assert cleaned["market_macro"] == "Para one.\n\nPara two."
    assert [s["ticker"] for s in cleaned["top_stories"]] == ["NVDA"]


def test_template_and_html_render() -> None:
    pack = _pack()
    sections = template_sections(pack)
    assert sections["source"] == "template"
    rows = briefing_email_rows(sections, pack)
    assert "Market &amp; macro" in rows
    assert "NVDA" in rows
    assert "Template text" in rows


def test_page_layout_has_grids_and_charts() -> None:
    pack = _pack()
    page = briefing_page_html(template_sections(pack), pack)
    assert page.count('class="grid-2"') == 2
    assert "Today&#x27;s moves" in page       # moves chart (NVDA, MSFT)
    assert "Closest to changing" in page      # distance-to-signal bars
    assert page.index("NVDA") < page.index("MSFT")  # largest gain first
