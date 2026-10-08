#!/usr/bin/env python3
"""Daily regime froth-score refresh (Stage 0 of the MDU cron job).

Refreshes every reachable source into the CSV history store, computes
today's froth score + market regime, and writes:

    data/outputs/regime/<YYYY-MM-DD>/froth_score.json

Usage:
    uv run workflows/regime_froth.py               # refresh + score
    uv run workflows/regime_froth.py --score-only  # score from store only
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
sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv(".env")

import pandas as pd

from lseg_quant.config import settings
from lseg_quant.regime.daily import (
    OUTPUT_DIRNAME,
    compute_froth_payload,
    compute_regime_payload,
)
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.lseg_feeds import spx_skew
from lseg_quant.regime.sources import fred
from workflows.regime_backfill import (
    backfill_cboe,
    backfill_cftc,
    backfill_finra,
    backfill_ici,
    backfill_spx,
)

logger = logging.getLogger("regime_froth")


def refresh_sources(store: HistoryStore, skip_skew: bool,
                    skip_cboe: bool = False) -> None:
    """Best-effort incremental refresh; each source is error-isolated."""
    steps: list[tuple[str, object]] = [
        ("fred", lambda: _refresh_fred(store)),
        ("spx", lambda: backfill_spx(store)),
        ("cftc", lambda: backfill_cftc(store)),
        ("finra", lambda: backfill_finra(store)),
        ("ici", lambda: backfill_ici(store)),
    ]
    for name, fn in steps:
        try:
            logger.info("[refresh] %s", name)
            fn()  # type: ignore[operator]
        except Exception as exc:  # noqa: BLE001 - source isolation
            logger.warning("[refresh] %s failed: %s", name, exc)

    if not skip_cboe:
        try:
            logger.info("[refresh] cboe monthly archives")
            backfill_cboe(store, lookback_days=0)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[refresh] cboe failed: %s", exc)

    if not skip_skew and os.environ.get("LSEG_CLIENT_ID"):
        try:
            with _mcp_client() as mcp:
                today = pd.Timestamp.now().strftime("%Y-%m-%d")
                skew = spx_skew(mcp, today)
                if skew is not None:
                    store.upsert("lseg_spx_skew",
                                 pd.Series([skew], index=[pd.Timestamp(today)],
                                           name="lseg_spx_skew"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[refresh] skew failed: %s", exc)


def _refresh_fred(store: HistoryStore) -> None:
    raw = fred.fetch_all()
    for name, series in raw.items():
        store.upsert(name, series)
    for name, series in fred.derived_metrics(raw).items():
        store.upsert(name, series)


def _mcp_client():
    from lseg_quant.mdu.mcp_client import MCPClient
    return MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                     client_secret=os.environ["LSEG_CLIENT_SECRET"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily froth-score refresh")
    parser.add_argument("--score-only", action="store_true",
                        help="Skip source refresh; score existing history")
    parser.add_argument("--skip-skew", action="store_true",
                        help="Skip the LSEG equity_vol_surface call")
    parser.add_argument("--skip-cboe", action="store_true",
                        help="Skip the CBOE archives (2019-only, so always "
                             "excluded as stale; saves ~2 min)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    store = HistoryStore()
    if not args.score_only:
        refresh_sources(store, skip_skew=args.skip_skew, skip_cboe=args.skip_cboe)

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
