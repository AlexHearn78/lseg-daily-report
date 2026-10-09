#!/usr/bin/env python3
"""Daily market regime and froth score (Stage 0 of the daily report).

Refreshes the LSEG series in the history store (backfilling ten years the
first time), takes today's S&P 500 skew point, computes the froth score and
market regime, and writes:

    data/outputs/regime/<YYYY-MM-DD>/froth_score.json

Every input comes from LSEG (see lseg_quant.regime.sources.lseg).

Usage:
    uv run workflows/regime_froth.py               # refresh + score
    uv run workflows/regime_froth.py --score-only  # score from the store only
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from dotenv import load_dotenv

load_dotenv(".env")

import pandas as pd

from lseg_quant.config import settings
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.regime.daily import (
    OUTPUT_DIRNAME,
    compute_froth_payload,
    compute_regime_payload,
)
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.lseg_feeds import spx_skew
from lseg_quant.regime.sources import lseg

logger = logging.getLogger("regime_froth")


def refresh_sources(store: HistoryStore, skip_skew: bool = False) -> None:
    """Best-effort refresh of every LSEG input; each source is error-isolated."""
    if not os.environ.get("LSEG_CLIENT_ID"):
        logger.warning("[refresh] LSEG_CLIENT_ID not set - scoring stored history only")
        return
    with MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                   client_secret=os.environ["LSEG_CLIENT_SECRET"]) as mcp:
        for name, error in lseg.refresh(store, mcp).items():
            logger.warning("[refresh] %s failed: %s", name, error)
        if skip_skew:
            return
        try:
            today = pd.Timestamp.now().strftime("%Y-%m-%d")
            skew = spx_skew(mcp, today)
            if skew is not None:
                store.upsert("lseg_spx_skew", pd.Series([skew], index=[pd.Timestamp(today)],
                                                        name="lseg_spx_skew"))
        except Exception as exc:  # noqa: BLE001 - source isolation
            logger.warning("[refresh] skew failed: %s", exc)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily froth-score refresh")
    parser.add_argument("--score-only", action="store_true",
                        help="Skip the LSEG refresh; score existing history")
    parser.add_argument("--skip-skew", action="store_true",
                        help="Skip the LSEG equity_vol_surface call")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    store = HistoryStore()
    if not args.score_only:
        refresh_sources(store, skip_skew=args.skip_skew)

    payload = compute_froth_payload(store=store)
    payload["market_regime"] = compute_regime_payload(store=store)

    out_dir = settings.output_root / OUTPUT_DIRNAME / payload["as_of"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "froth_score.json"
    out_path.write_text(json.dumps(payload, indent=2))

    print(f"froth score {payload['composite_score']} ({payload['band']}) "
          f"velocity={payload['velocity_flag']} "
          f"regime={payload['market_regime'].get('regime')}")
    print(f"written: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
