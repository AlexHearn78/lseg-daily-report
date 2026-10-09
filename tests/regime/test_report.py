"""Tests for the froth click-through detail and its payload."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from lseg_quant.briefing.render import briefing_email_rows, briefing_page_html, template_sections
from lseg_quant.regime.daily import _ending_today, compute_froth_payload
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.report import (
    froth_detail_html,
    pillar_detail_html,
    regime_detail_html,
    svg_line_chart,
)
from lseg_quant.regime.score import PILLAR_WEIGHTS


def _store(tmp_path: Path) -> HistoryStore:
    store = HistoryStore(tmp_path)
    idx = pd.bdate_range(end="2026-09-10", periods=1500)
    store.upsert("hy_oas", pd.Series([5.0 - i / 1000 for i in range(1500)], index=idx))
    q = pd.date_range("2014-01-01", "2026-04-01", freq="QS")
    store.upsert("margin_loans_z1", pd.Series([400_000 + 5_000 * i for i in range(len(q))],
                                              index=q, dtype=float))
    return store


def _payload(tmp_path: Path) -> dict:
    return compute_froth_payload(as_of=pd.Timestamp("2026-09-11"), store=_store(tmp_path))


def _pack(froth: dict) -> dict:
    return {"macro": {"froth": froth}, "tickers": [], "top_stories": [],
            "earnings_watch": [], "closest_to_changing": [], "summary": {}}


def test_payload_carries_raw_values_and_history(tmp_path: Path):
    payload = _payload(tmp_path)
    z1 = payload["metrics"]["margin_loans_z1"]
    assert z1["value"] == 645_000.0
    assert z1["last_date"] == "2026-04-01"
    assert z1["method"] == "percentile"
    assert payload["metrics"]["hy_oas"]["inverted"] is True

    hist = payload["history"]
    # Charts end on the reported score, not the rebuilt approximation.
    assert hist["composite"][-1] == ["2026-09-11", payload["composite_score"]]
    assert hist["pillars"]["leverage"][-1] == ["2026-09-11", payload["pillar_scores"]["leverage"]]
    assert "spx_close" not in hist  # nothing stored, nothing charted


def test_ending_today_replaces_current_month():
    pts = [["2026-08-31", 50.0], ["2026-09-10", 55.0]]
    assert _ending_today(pts, pd.Timestamp("2026-09-11"), 58.1) == [
        ["2026-08-31", 50.0], ["2026-09-11", 58.1]]
    assert _ending_today(pts, pd.Timestamp("2026-09-11"), None) == [["2026-08-31", 50.0]]


def test_detail_html_renders(tmp_path: Path):
    payload = _payload(tmp_path)
    leverage = pillar_detail_html(payload, "leverage")
    assert "$645bn" in leverage and "Margin loans (Fed Z.1)" in leverage and "<svg" in leverage
    assert "Weight" in froth_detail_html(payload)
    assert "Risk off" in regime_detail_html(payload)


def test_dashboard_bars_click_through_in_report_only(tmp_path: Path):
    payload = _payload(tmp_path)
    pack = _pack(payload)
    sections = template_sections(pack)
    details = {p: pillar_detail_html(payload, p) for p in PILLAR_WEIGHTS}

    page = briefing_page_html(sections, pack, details)
    for p in ("valuation", "leverage"):
        assert f"toggleDetail(this, 'risk-{p}')" in page
        assert f'id="risk-{p}"' in page
    # No details: the page and the email carry no click-through markup.
    assert "toggleDetail" not in briefing_page_html(sections, pack)
    assert "toggleDetail" not in briefing_email_rows(sections, pack)


def test_old_payload_without_details_still_renders():
    old = {"as_of": "2026-09-11", "composite_score": 58.1, "band": "balanced",
           "pillar_scores": {"valuation": 90.4, "leverage": 97.2,
                             "positioning": None, "liquidity": 39.5},
           "metrics": {"hy_oas": {"score": 90.4, "pillar": "valuation",
                                  "low_confidence": False}}}
    assert "were not saved" in pillar_detail_html(old, "valuation")
    assert "positioning" in froth_detail_html(old).lower()
    assert "Regime" in regime_detail_html(old)


def test_chart_breaks_line_at_data_gap():
    pts = [["2018-01-01", 1.0], ["2018-04-01", 2.0], ["2018-07-01", 3.0],
           ["2021-01-01", 4.0], ["2021-04-01", 5.0]]
    svg = svg_line_chart([("x", "#3987e5", pts)])
    path = svg.split('<path d="')[1].split('"')[0]
    assert path.count("M") == 2
