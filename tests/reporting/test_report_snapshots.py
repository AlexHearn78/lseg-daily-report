"""The attached report must render every snapshot shape the batch records."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workflows"))

from mdu_report import TickerRun, _drilldown_html  # noqa: E402


def test_drilldown_handles_list_and_dict_snapshots() -> None:
    # Shapes recorded by mdu_run since 12 Sep: news_top is a list, the others dicts.
    records = [
        {"type": "data_snapshot", "label": "nvda_prices", "data": {"rows": 750}},
        {"type": "data_snapshot", "label": "price_tail",
         "data": {"dates": ["2026-09-14"], "closes": [180.1]}},
        {"type": "data_snapshot", "label": "news_top",
         "data": [{"date": "2026-09-14", "headline": "Chip stocks rally"}]},
        {"type": "data_snapshot", "label": "analyst_scores",
         "data": {"composite": 0.1, "dimension_scores": {"valuation": 0.2}}},
        {"type": "final_trade", "action": "HOLD", "confidence": 0.7, "reasoning": ["ok"]},
    ]
    html = _drilldown_html("NVDA", TickerRun(ticker="NVDA", group="holding", records=records, failed=False))
    assert "news top" in html and "1 items" in html
    assert "analyst scores" in html
