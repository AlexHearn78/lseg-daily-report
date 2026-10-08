#!/usr/bin/env python3
"""Build the nightly briefing pack (the facts behind the report narrative).

Runs after the ticker batch. Reads the run's audit trail, adds price moves,
recent earnings-call excerpts and macro headlines from LSEG, and writes:

    data/outputs/briefing/<YYYYMMDD>/pack.json
    data/outputs/briefing/<YYYYMMDD>/sections_template.json

The Claude step in .github/workflows/mdu-daily.yml then writes
sections.json next to them; mdu_report falls back to the template when that
file is missing or malformed.

Usage:
    uv run workflows/mdu_briefing.py [--date 20260911] [--no-mcp]
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv

load_dotenv(".env")

import pandas as pd

from lseg_quant.briefing.pack import TickerInput, build_pack, composite_from_hold_confidence
from lseg_quant.briefing.render import template_sections
from lseg_quant.regime.daily import load_latest_context
from lseg_quant.regime.history import HistoryStore
from mdu_email import latest_run_date
from mdu_report import TickerRun, load_run

logger = logging.getLogger("mdu_briefing")

BRIEFING_ROOT = Path("data/outputs/briefing")


def ticker_input(tr: TickerRun) -> TickerInput:
    """Extract what the pack needs from one ticker's audit records."""
    if tr.failed:
        return TickerInput(key=tr.ticker, failed=True)
    snaps = tr.get_data_snapshots()
    ft = tr.get_final_trade() or {}
    t = TickerInput(key=tr.ticker, action=ft.get("action", "HOLD"),
                    confidence=ft.get("confidence"), drivers=list(tr.get_reasoning())[:4])

    scores = snaps.get("analyst_scores")
    if isinstance(scores, dict) and scores.get("composite") is not None:
        t.composite, t.composite_source = float(scores["composite"]), "recorded"
    else:
        llm = tr.get_llm_decision() or {}
        if llm.get("action") == "HOLD" and llm.get("confidence") is not None:
            t.composite = composite_from_hold_confidence(float(llm["confidence"]))
            t.composite_source = "derived"

    tail = snaps.get("price_tail")
    if isinstance(tail, dict) and tail.get("closes") and len(tail.get("dates", [])) == len(tail["closes"]):
        t.closes = pd.Series(tail["closes"], index=pd.to_datetime(tail["dates"]), dtype=float)
    news = snaps.get("news_top")
    if isinstance(news, list):
        t.headlines = [h for h in news if isinstance(h, dict) and h.get("headline")]
    return t


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build the nightly briefing pack")
    p.add_argument("--date", type=str, default=None,
                   help="Run date (YYYYMMDD or YYYY-MM-DD, default: latest run)")
    p.add_argument("--no-mcp", action="store_true",
                   help="Skip LSEG lookups (prices/earnings/headlines from the audit only)")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    date_raw = (args.date or latest_run_date()).replace("-", "")
    run = load_run(date_raw)
    inputs = [ticker_input(tr) for tr in run.tickers.values()]

    use_mcp = not args.no_mcp and bool(os.environ.get("LSEG_CLIENT_ID"))
    if use_mcp:
        from lseg_quant.mdu.mcp_client import MCPClient
        client_cm = MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                              client_secret=os.environ["LSEG_CLIENT_SECRET"])
    else:
        client_cm = contextlib.nullcontext()
    with client_cm as mcp:
        pack = build_pack(run.date_str, inputs, load_latest_context(), HistoryStore(), mcp=mcp)

    out_dir = BRIEFING_ROOT / date_raw
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pack.json").write_text(json.dumps(pack, indent=2, default=str))
    (out_dir / "sections_template.json").write_text(
        json.dumps(template_sections(pack), indent=2))

    stories = ", ".join(s["key"] for s in pack["top_stories"]) or "none"
    earnings = ", ".join(e["key"] for e in pack["earnings_watch"]) or "none"
    print(f"briefing pack: {out_dir / 'pack.json'} | top stories: {stories} | "
          f"recent earnings: {earnings} | macro headlines: {len(pack['macro_headlines'])}")

    gh_output = os.environ.get("GITHUB_OUTPUT")
    if gh_output:
        with open(gh_output, "a") as fh:
            fh.write(f"dir={out_dir}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
