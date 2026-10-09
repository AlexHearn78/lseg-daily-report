"""Offline tests for full-text news, cited sources and the report's price charts."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from lseg_quant.briefing.pack import (
    TickerInput,
    _clean_stories,
    build_charts,
    build_pack,
    macro_block,
)
from lseg_quant.briefing.render import (
    briefing_email_rows,
    briefing_page_html,
    template_sections,
    validate_sections,
)
from lseg_quant.reporting.theme import stat_tiles

SKILL = Path(__file__).resolve().parents[2] / ".claude" / "skills" / "daily-briefing"


def _closes(n: int = 300, end: str = "2026-09-11", jump: float = 0.03) -> pd.Series:
    rng = np.random.default_rng(7)
    rets = rng.normal(0, 0.01, n)
    rets[-1] = jump
    return pd.Series(100 * np.cumprod(1 + rets), index=pd.bdate_range(end=end, periods=n))


class _Store:
    def __init__(self, series: dict[str, pd.Series] | None = None) -> None:
        self.series = series or {}

    def read(self, name: str) -> pd.Series:
        return self.series.get(name, pd.Series(dtype=float))


def test_clean_stories_puts_company_stories_first_and_trims() -> None:
    items = [
        {"headlineText": "Sector wrap", "story": "Chips were mixed today.", "source": "RTRS",
         "firstCreated": "2026-09-11T10:00:00Z"},
        {"headlineText": "DIARY-Week ahead", "story": "NVIDIA reports next week."},
        {"headlineText": "Nvidia expands in Australia", "story": "NVIDIA said ... " + "x" * 5000,
         "source": "RTRS", "firstCreated": "2026-09-10T08:00:00Z"},
        {"headlineText": "No body here"},
    ]
    stories = _clean_stories(items, "NVDA", limit=3)
    assert [s["headline"] for s in stories] == ["Nvidia expands in Australia", "Sector wrap"]
    assert stories[0]["date"] == "2026-09-10" and stories[0]["source"] == "RTRS"
    assert len(stories[0]["text"]) == 2500


def test_macro_block_adds_ftse_all_world_from_lseg_closes() -> None:
    block = macro_block(None, _Store(), {"ftse_all_world": _closes(jump=0.01)})
    aw = block["ftse_all_world"]
    assert aw["as_of"] == "2026-09-11"
    assert aw["ret_1d_pct"] == 1.0
    assert aw["ret_1m_pct"] is not None
    assert "sp500" not in block  # nothing in the store


def test_charts_are_capped_at_the_run_date_and_a_year_back() -> None:
    long = _closes(n=600, end="2026-10-07")
    charts = build_charts("2026-09-11", [TickerInput(key="NVDA", closes=long),
                                         TickerInput(key="TSLA", failed=True)],
                          _Store({"spx_close": long}), {"ftse_all_world": long})
    pts = charts["tickers"]["NVDA"]
    assert pts[-1][0] <= "2026-09-11" and pts[0][0] >= "2025-09-10"
    assert set(charts["indices"]) == {"sp500", "ftse_all_world"}
    assert "TSLA" not in charts["tickers"]


def test_charts_include_rates_with_the_curve_in_basis_points() -> None:
    idx = pd.bdate_range(end="2026-09-11", periods=300)
    store = _Store({"dgs10": pd.Series(4.5, index=idx), "dgs2": pd.Series(4.1, index=idx),
                    "hy_oas": pd.Series(3.2, index=idx)})
    ind = build_charts("2026-09-11", [], store)["indices"]
    assert ind["us_10y_yield"][-1] == ["2026-09-11", 4.5]
    assert ind["curve_2s10s_bp"][-1] == ["2026-09-11", 40.0]
    assert ind["high_yield_spread"][-1][1] == 3.2


def test_rate_tiles_open_their_charts() -> None:
    pack = _pack_with_story()
    pack["macro"].update(us_10y_yield={"last_pct": 4.95, "chg_1m_bp": 25}, curve_2s10s_bp=39,
                         high_yield_spread={"last_pct": 2.7, "chg_1m_bp": -2})
    days = [str(d.date()) for d in pd.bdate_range(end="2026-09-11", periods=250)]
    rising = [[d, 4.0 + i / 250] for i, d in enumerate(days)]
    charts = {"indices": {"us_10y_yield": rising,
                          "curve_2s10s_bp": [[d, 39.0 - i / 10] for i, d in enumerate(days)],
                          "high_yield_spread": [[d, 2.7] for d in days]}}
    page = briefing_page_html(template_sections(pack), pack, charts=charts)
    for key in ("us_10y_yield", "curve_2s10s_bp", "high_yield_spread"):
        assert f"toggleDetail(this, 'px-{key}')" in page
    assert "12M +100bp" in page            # 4.0% to 5.0% over the year
    assert "inverted below" in page        # zero line on the curve chart


def _pack_with_story() -> dict:
    tickers = [TickerInput(key="NVDA", composite=0.2, closes=_closes(jump=0.05)),
               TickerInput(key="MSFT", composite=-0.1, closes=_closes(jump=0.001))]
    pack = build_pack("2026-09-11", tickers, froth=None, store=_Store())
    pack["top_stories"][0]["stories"] = [
        {"date": "2026-09-10", "source": "RTRS", "headline": "Nvidia expands in Australia", "text": "..."}]
    return pack


def test_cited_sources_must_have_been_read() -> None:
    pack = _pack_with_story()
    story = {"ticker": "NVDA", "title": "t", "body": "b", "sources": [
        {"source": "RTRS", "date": "2026-09-10", "headline": "Nvidia expands in Australia"},
        {"source": "RTRS", "date": "2026-09-11", "headline": "Invented headline"},
        {"source": "RTRS", "date": "2026-09-11", "headline": "Found by a live lookup"}]}
    raw = {"market_macro": "One.\n\nTwo.", "top_stories": [story]}
    kept = validate_sections(raw, pack)["top_stories"][0]["sources"]
    assert [s["headline"] for s in kept] == ["Nvidia expands in Australia"]
    lookups = [{"ticker": "NVDA", "headline": "Found by a live lookup"}]
    kept = validate_sections(raw, pack, lookups)["top_stories"][0]["sources"]
    assert [s["headline"] for s in kept] == ["Nvidia expands in Australia", "Found by a live lookup"]
    sections = validate_sections(raw, pack, lookups)
    assert "Sources: Reuters, 10 Sep: Nvidia expands in Australia" in briefing_email_rows(sections, pack)


def test_price_click_throughs_in_report_only() -> None:
    pack = _pack_with_story()
    pack["macro"]["sp500"] = {"ret_1d_pct": 0.5, "ret_1m_pct": 1.0}
    sections = template_sections(pack)
    pts = [[str(d.date()), round(float(v), 2)] for d, v in _closes().tail(250).items()]
    charts = {"indices": {"sp500": pts}, "tickers": {"NVDA": pts}}

    page = briefing_page_html(sections, pack, charts=charts)
    assert "toggleDetail(this, 'px-sp500')" in page
    assert "toggleDetail(this, 'px-NVDA')" in page
    assert "MSFT" in page and "px-MSFT" not in page   # no series, no click-through
    for html in (briefing_page_html(sections, pack), briefing_email_rows(sections, pack)):
        assert "toggleDetail" not in html


def test_stat_tiles_unchanged_without_cell_attrs() -> None:
    tiles = [("S&P 500 1D", "+0.2%", "#fff", None)]
    assert stat_tiles(tiles) == stat_tiles(tiles, cell_attrs=None)
    assert '<td width="100%" style=' in stat_tiles(tiles)


def test_skill_gate_self_test_and_evals() -> None:
    for script in (SKILL / "scripts" / "check_sections.py", SKILL / "evals" / "run_evals.py"):
        args = [sys.executable, str(script)] + (["--self-test"] if "check" in script.name else [])
        out = subprocess.run(args, capture_output=True, text=True)
        assert out.returncode == 0, out.stdout + out.stderr

