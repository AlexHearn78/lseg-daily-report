"""Consensus store and post-results earnings signal (no network)."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest

from lseg_quant.signals.consensus import append, load
from lseg_quant.signals.earnings import PEAK_WEIGHT, earnings_signal

ANNOUNCE = "2026-09-02T20:16:00+00:00"


def _est(day: str, ptype: str, end: str, measure: str, value: float) -> dict:
    return {"snapshot_date": day, "kind": "estimate", "period_type": ptype, "period_end": end,
            "measure": measure, "value": value, "announce_utc": ""}


def _actual(measure: str, value: float) -> dict:
    return {"snapshot_date": "2026-09-03", "kind": "actual", "period_type": "Quarter",
            "period_end": "2026-07-31", "measure": measure, "value": value, "announce_utc": ANNOUNCE}


def _history() -> list[dict]:
    return [
        _est("2026-09-01", "Quarter", "2026-07-31", "EPS", 3.00),   # pre-results
        _est("2026-09-01", "Quarter", "2026-07-31", "REV", 29000.0),
        _est("2026-09-01", "Year", "2026-10-31", "EPS", 11.00),
        _est("2026-09-01", "Year", "2027-10-31", "EPS", 18.00),
        _est("2026-09-10", "Quarter", "2026-07-31", "EPS", 3.20),   # after results: ignored for surprise
        _est("2026-09-10", "Year", "2026-10-31", "EPS", 11.55),
        _est("2026-09-10", "Year", "2027-10-31", "EPS", 18.90),
        _actual("EPS", 3.30), _actual("REV", 29580.0),
    ]


def test_store_appends_once(tmp_path: Path) -> None:
    rows = _history()
    assert append(tmp_path, "AVGO", rows) == len(rows)
    assert append(tmp_path, "AVGO", rows) == 0
    later = dict(_actual("EPS", 3.30), snapshot_date="2026-09-11")  # same actual seen again
    assert append(tmp_path, "AVGO", [later]) == 0
    assert load(tmp_path, "AVGO")[0]["value"] == 3.00


def test_signal_uses_pre_results_consensus_and_revisions() -> None:
    sig = earnings_signal("AVGO", _history(), dt.date(2026, 9, 14), composite=0.10)
    assert sig["trading_days_since"] == 8
    assert sig["surprise"]["EPS"] == {"actual": 3.30, "consensus": 3.0, "pct": 10.0,
                                      "vintage": "pre_results", "consensus_date": "2026-09-01"}
    assert sig["surprise"]["REV"]["pct"] == pytest.approx(2.0)
    assert sig["revisions"]["FY1"]["pct"] == 5.0 and sig["revisions"]["FY2"]["pct"] == 5.0
    # surprise = mean(10/10, 2/5) = 0.7; revisions = 1.0 -> (0.25*0.7 + 0.40*1.0) / 0.65
    assert sig["score"] == pytest.approx(0.885, abs=0.001)
    assert sig["weight"] == pytest.approx(PEAK_WEIGHT * (1 - 8 / 60), abs=0.001)
    assert sig["shadow_composite"] > sig["composite"]


def test_signal_falls_back_to_latest_published_and_expires() -> None:
    no_history = [r for r in _history() if r["snapshot_date"] != "2026-09-01"]
    sig = earnings_signal("AVGO", no_history, dt.date(2026, 9, 14), composite=0.0)
    assert sig["surprise"]["EPS"]["vintage"] == "latest_published"
    assert sig["revisions"] == {} and "revisions" not in sig["components"]
    assert earnings_signal("AVGO", _history(), dt.date(2026, 12, 31), composite=0.0) is None


def test_outlier_surprise_counts_at_half_strength() -> None:
    rows = [r for r in _history() if not (r["kind"] == "actual" and r["measure"] == "REV")]
    rows = [dict(r, value=9.90) if r["kind"] == "actual" else r for r in rows]  # +230% vs 3.00
    sig = earnings_signal("AMZN", rows, dt.date(2026, 9, 14), composite=0.0)
    assert sig["surprise"]["EPS"]["outlier"] is True
    assert sig["components"]["surprise"] == 0.5
    assert any("one-off" in n for n in sig["notes"])


def test_shadow_can_change_a_borderline_rating() -> None:
    sig = earnings_signal("VOW3", _history(), dt.date(2026, 9, 3), composite=0.24)
    assert sig["action"] == "HOLD" and sig["shadow_action"] == "BUY"
