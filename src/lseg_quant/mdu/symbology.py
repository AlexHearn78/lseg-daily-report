"""Resolve universe RICs to company name, organisation PermID and quote currency.

Uses the LSEG ``symbology_lookup`` MCP tool. Identifiers only: nothing here is
market data, so the result can be committed as ``config/universe.resolved.json``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from lseg_quant.mdu.mcp_client import MCPClient

logger = logging.getLogger(__name__)

_BATCH = 50  # symbology_lookup accepts at most 50 terms per call


def parse_lookup(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Map each looked-up RIC to its details, or to an ``error`` entry."""
    out: dict[str, dict[str, Any]] = {}
    for row in payload.get("results") or []:
        term = row.get("_term")
        if not term:
            continue
        if not row.get("_found"):
            out[term] = {"error": "not found"}
            continue
        if row.get("RIC") != term:
            out[term] = {"error": f"LSEG matched a different RIC ({row.get('RIC')})"}
            continue
        if row.get("AssetState") not in (None, "AC"):
            out[term] = {"error": f"listing is not active (AssetState {row.get('AssetState')})"}
            continue
        permid = row.get("IssuerOAPermID")
        out[term] = {
            "ric": term,
            "name": str(row.get("DocumentTitle", term)).split(",")[0].strip(),
            "permid": int(permid) if permid else None,
            "currency": row.get("Currency"),
            "exchange": row.get("ExchangeName"),
        }
    return out


def resolve_rics(mcp: MCPClient, rics: list[str]) -> dict[str, dict[str, Any]]:
    """Look up every RIC; a RIC LSEG does not return is reported as an error."""
    out: dict[str, dict[str, Any]] = {}
    for i in range(0, len(rics), _BATCH):
        batch = rics[i:i + _BATCH]
        resp = mcp.call_tool("symbology_lookup", {"terms": batch, "entityType": "equity"})
        text = MCPClient._extract_text(resp)
        out.update(parse_lookup(json.loads(text)))
    for ric in rics:
        out.setdefault(ric, {"error": "no row returned"})
    return out
