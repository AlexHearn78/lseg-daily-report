#!/usr/bin/env python3
"""Backfill the froth score's history from LSEG and preview the composite.

The daily refresh already backfills ten years of any empty LSEG series. The
one input it cannot backfill in a single call is the S&P 500 put skew, which
is replayed here from weekly historical volatility surfaces. Writes a
manifest with each series' coverage and a reconstructed composite.

Usage:
    uv run workflows/regime_backfill.py                          # everything
    uv run workflows/regime_backfill.py --only lseg_skew --skew-years 4
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(".env")

from lseg_quant.config import settings
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.regime.daily import INVERT_OF, PILLAR_OF
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.lseg_feeds import LsegSkewHistory
from lseg_quant.regime.score import historical_composite
from lseg_quant.regime.sources import lseg

logger = logging.getLogger("regime_backfill")


def _client() -> MCPClient:
    return MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                     client_secret=os.environ["LSEG_CLIENT_SECRET"])


def backfill_series(store: HistoryStore) -> None:
    """Ten years of every LSEG market and macro series (incremental if present)."""
    with _client() as mcp:
        for name, error in lseg.refresh(store, mcp).items():
            logger.error("%s failed: %s", name, error)


def backfill_lseg_skew(store: HistoryStore, years: int) -> None:
    """Weekly SPX skew points replayed from historical calculation dates."""
    end = dt.date.today()
    start = end - dt.timedelta(weeks=int(years * 52))
    dates = [str(d.date()) for d in pd.date_range(start, end, freq="W-WED") if d.date() <= end]
    logger.info("[backfill] LSEG SPX skew (%d weekly points)", len(dates))
    try:
        with _client() as mcp:
            series = LsegSkewHistory(mcp).fetch(dates)
        store.upsert("lseg_spx_skew", series)
    except Exception as exc:  # noqa: BLE001
        logger.error("LSEG skew backfill failed: %s", exc)


def preview_composite(store: HistoryStore, out_dir: Path) -> dict[str, object]:
    """Reconstruct the historical composite from whatever history exists."""
    histories = {name: s for name in PILLAR_OF if len(s := store.read(name))}
    if not histories:
        return {}
    composite = historical_composite(histories, PILLAR_OF, INVERT_OF)
    if not len(composite):
        return {}
    path = out_dir / "historical_composite.csv"
    pd.DataFrame({"date": composite.index.strftime("%Y-%m-%d"),
                  "value": composite.round(2).values}).to_csv(path, index=False)
    return {
        "observations": len(composite),
        "start": str(composite.index.min().date()),
        "end": str(composite.index.max().date()),
        "latest_score": round(float(composite.iloc[-1]), 1),
        "series_csv": str(path),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Froth score history backfill (LSEG)")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated subset: series,lseg_skew")
    parser.add_argument("--skew-years", type=int, default=3,
                        help="Years of weekly SPX skew history to replay (default 3)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    selected = set(args.only.split(",")) if args.only else {"series", "lseg_skew"}
    store = HistoryStore()
    if "series" in selected:
        backfill_series(store)
    if "lseg_skew" in selected:
        backfill_lseg_skew(store, args.skew_years)

    out_dir = settings.output_root / "regime_backfill" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    coverage = store.coverage()
    preview = preview_composite(store, out_dir)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps({"coverage": coverage, "composite_preview": preview}, indent=2))

    print("\n=== Backfill coverage ===")
    for name, info in sorted(coverage.items()):
        print(f"  {name:24s} {info['rows']:>7} rows  {info['start']} .. {info['end']}")
    if preview.get("latest_score") is not None:
        print(f"\n  Reconstructed composite (latest): {preview['latest_score']}")
    print(f"\nManifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
