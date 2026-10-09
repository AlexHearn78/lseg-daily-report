#!/usr/bin/env python3
"""Write a Claude Code MCP config for the LSEG endpoint (GitHub Actions).

Gets a service-account access token (client credentials, ~24h), registers it
as a masked secret with the runner, and writes an HTTP MCP server entry
named ``lseg`` so the Claude step can call LSEG tools as mcp__lseg__*.
The token is never printed in clear.

Usage:
    uv run workflows/lseg_mcp_config.py --out "$RUNNER_TEMP/lseg-mcp.json"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lseg_quant.mdu.mcp_client import MCPClient


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Write LSEG MCP config for Claude Code")
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)

    client = MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                       client_secret=os.environ["LSEG_CLIENT_SECRET"])
    token = client._ensure_token()  # same OAuth flow the pipeline uses
    print(f"::add-mask::{token}")
    config = {"mcpServers": {"lseg": {
        "type": "http",
        "url": client.endpoint,
        "headers": {"Authorization": f"Bearer {token}"},
    }}}
    args.out.write_text(json.dumps(config))
    args.out.chmod(0o600)
    print(f"wrote MCP config for {client.endpoint} to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
