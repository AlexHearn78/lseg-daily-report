#!/usr/bin/env python3
"""Store today's IBES consensus snapshot for every ticker (daily job step).

Appends to data/raw/consensus/<TICKER>.csv (see lseg_quant.signals.consensus),
which GitHub Actions keeps between runs in the cache. One batched IBES call
per ticker; a failure skips that ticker only.

Usage:
    uv run workflows/consensus_snapshot.py
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(".env")

from lseg_quant.mdu.config import UNIVERSE
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.signals.consensus import STORE_DIR, append, fetch_snapshot

logger = logging.getLogger("consensus_snapshot")


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s", stream=sys.stderr)
    today = dt.datetime.now(dt.timezone.utc).date()
    stored, failed = 0, []
    with MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                   client_secret=os.environ["LSEG_CLIENT_SECRET"]) as mcp:
        for key in UNIVERSE:
            try:
                rows = fetch_snapshot(mcp, key, today)
            except Exception as exc:  # noqa: BLE001 - one ticker must not stop the rest
                logger.warning("%s: snapshot failed: %s", key, exc)
                failed.append(key)
                continue
            if not rows:
                failed.append(key)
                continue
            stored += append(STORE_DIR, key, rows)
    print(f"consensus snapshot {today}: {stored} new rows; "
          f"no data for {', '.join(failed) if failed else 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
