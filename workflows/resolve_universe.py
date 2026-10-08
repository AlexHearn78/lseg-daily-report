#!/usr/bin/env python3
"""Check every RIC in config/universe.yaml against LSEG and record its details.

Writes config/universe.resolved.json: company name, organisation PermID (for
earnings-call transcripts) and quote currency per name. Run after every edit
to universe.yaml and commit both files. Exits 1 if any RIC fails to resolve.

    uv run workflows/resolve_universe.py
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pandas_market_calendars as mcal
from dotenv import load_dotenv
load_dotenv()

from lseg_quant.mdu.config import CONFIG_DIR, MDUSettings, load_universe
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.mdu.symbology import resolve_rics

logger = logging.getLogger("resolve_universe")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    universe = load_universe()
    s = MDUSettings()
    if not (s.lseg_client_id and s.lseg_client_secret):
        logger.error("LSEG_CLIENT_ID and LSEG_CLIENT_SECRET must be set (see .env.example)")
        return 1
    with MCPClient(client_id=s.lseg_client_id, client_secret=s.lseg_client_secret) as mcp:
        found = resolve_rics(mcp, [info.ric for info in universe.values()])

    calendars = set(mcal.get_calendar_names())
    resolved: dict[str, dict] = {}
    failures = 0
    for key, info in universe.items():
        if info.exchange not in calendars:
            failures += 1
            logger.error("%-6s exchange %r is not a known calendar (US listings use XNYS)",
                         key, info.exchange)
            continue
        row = found[info.ric]
        if "error" in row:
            failures += 1
            logger.error("%-6s %-10s %s", key, info.ric, row["error"])
            continue
        resolved[key] = row
        logger.info("%-6s %-10s %-28s %-4s PermID %s", key, info.ric, row["name"][:28],
                    row["currency"], row["permid"])

    out = CONFIG_DIR / "universe.resolved.json"
    out.write_text(json.dumps(resolved, indent=2, sort_keys=True) + "\n")
    logger.info("Wrote %s (%d of %d names)", out, len(resolved), len(universe))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
