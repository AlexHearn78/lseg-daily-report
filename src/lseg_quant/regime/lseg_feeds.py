"""LSEG qa_macroeconomic + LFA curve feeds (history-bearing macro series).

The ``qa_macroeconomic`` MCP tool exposes Datastream-style indicators with
full history via a list/latest/series workflow. Curve tools
(``interest_rate_curve``, ``credit_curve``, ``inflation_curve``) are
point-in-time only — they give today's value to rank against stored history,
never the history itself.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pandas as pd

from lseg_quant.mdu.mcp_client import MCPClient, MCPError

logger = logging.getLogger(__name__)


class LsegMacroFeeds:
    """Thin typed wrapper over the qa_macroeconomic MCP tool."""

    def __init__(self, mcp: MCPClient) -> None:
        self._mcp = mcp

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def find_indicators(self, description: str | None = None,
                        mnemonic: str | None = None,
                        market: str | None = None,
                        frequency: str | None = None,
                        limit: int = 25) -> list[dict[str, Any]]:
        """Search the macro indicator catalogue (wildcards supported)."""
        options: dict[str, Any] = {"limit": limit}
        if description:
            options["description"] = description
        if mnemonic:
            options["mnemonic"] = mnemonic
        if market:
            options["marketDescription"] = market
        if frequency:
            options["frequency"] = frequency
        return self._request({"dataType": "list", "options": options})

    def latest(self, mnemonic: str) -> dict[str, Any] | None:
        rows = self._request({"dataType": "latest", "options": {"mnemonic": mnemonic}})
        return rows[0] if rows else None

    def series(self, mnemonic: str, from_date: str | None = None,
               to_date: str | None = None) -> pd.Series:
        """Full history for one indicator, paging through in ascending order.

        The endpoint caps page size (~200 obs); we loop with a moving window.
        """
        chunks: list[pd.DataFrame] = []
        cursor = from_date
        while True:
            options: dict[str, Any] = {
                "mnemonic": mnemonic,
                "order": "asc",
                "limit": 200,
            }
            if cursor:
                options["from"] = cursor
            if to_date:
                options["to"] = to_date
            rows = self._request({"dataType": "series", "options": options})
            if not rows:
                break
            df = pd.DataFrame(rows)
            df["period"] = pd.to_datetime(df["period"], errors="coerce")
            df["value"] = pd.to_numeric(df["value"], errors="coerce")
            df = df.dropna(subset=["period"])
            if not len(df):
                break
            chunks.append(df[["period", "value"]])
            last = df["period"].max()
            if to_date and last >= pd.Timestamp(to_date):
                break
            if len(df) < 200:
                break
            cursor = (last + pd.Timedelta(days=1)).strftime("%Y-%m-%d")

        if not chunks:
            logger.warning("lseg_macro[%s]: no data returned", mnemonic)
            return pd.Series(dtype=float, name=mnemonic)
        all_rows = pd.concat(chunks).drop_duplicates("period").set_index("period")["value"]
        out = all_rows.sort_index()
        out.name = mnemonic
        logger.info("lseg_macro[%s]: %d obs (%s .. %s)", mnemonic, len(out),
                    out.index.min().date(), out.index.max().date())
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _request(self, request_body: dict[str, Any]) -> list[dict[str, Any]]:
        try:
            resp = self._mcp.call_tool("qa_macroeconomic",
                                       {"requests": [request_body]})
        except MCPError as exc:
            logger.warning("qa_macroeconomic call failed (%s): %s",
                           request_body.get("dataType"), exc)
            return []
        result = resp.get("result", {})
        if result.get("isError"):
            logger.warning("qa_macroeconomic isError for %s",
                           request_body.get("options"))
            return []
        raw = ""
        content = result.get("content", [])
        if content and isinstance(content[0], dict):
            raw = str(content[0].get("text", ""))
        return _parse_macro_payload(raw)


def _parse_macro_payload(raw: str) -> list[dict[str, Any]]:
    """Extract row dicts from whatever shape the tool returns."""
    if not raw.strip():
        return []
    # Direct JSON payload
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_markdown_table(raw)
    # The tool answers [{"dataType": ..., "options": ..., "response": {"data": ...}}]:
    # rows for "list"/"series", a single observation dict for "latest".
    if isinstance(data, list) and data and all(isinstance(d, dict) and "response" in d for d in data):
        rows: list[dict[str, Any]] = []
        for entry in data:
            inner = (entry.get("response") or {}).get("data")
            if isinstance(inner, list):
                rows.extend(inner)
            elif isinstance(inner, dict):
                rows.append(inner)
        return rows
    if isinstance(data, dict):
        for key in ("rows", "data", "observations", "series"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return [data]
    if isinstance(data, list):
        return data
    return []


def _parse_markdown_table(raw: str) -> list[dict[str, Any]]:
    """Fallback parser when the tool renders results as a markdown table."""
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("|")]
    if len(lines) < 2:
        return []
    header = [c.strip() for c in lines[0].strip("|").split("|")]
    rows: list[dict[str, Any]] = []
    for line in lines[1:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        if all(set(c) <= set("-: ") for c in cells):  # separator row
            continue
        rows.append(dict(zip(header, cells)))
    return rows


# ---------------------------------------------------------------------------
# SPX skew (positioning pillar primary input)
# ---------------------------------------------------------------------------

def spx_skew(mcp: MCPClient, as_of: str,
             moneyness_low: float = 0.90,
             moneyness_high: float = 1.00,
             target_expiry_days: int = 90) -> float | None:
    """25-ish delta proxy: IV(0.90 moneyness) - IV(1.00) at the expiry nearest
    *target_expiry_days* out, computed on the SPX surface as of *as_of*.

    High skew (expensive downside protection) signals hedging demand /
    fear; flat or negative skew signals complacency — so downstream this
    series is INVERTED when scoring froth.
    """
    args = {
        "instrument": ".SPX@RIC",
        "surfaces": {"dates": [as_of], "date_format": "Date",
                     "strike_format": "Moneyness"},
        "smiles": None,
    }
    try:
        resp = mcp.call_tool("equity_vol_surface", args)
    except MCPError as exc:
        logger.warning("spx_skew[%s]: call failed: %s", as_of, exc)
        return None
    result = resp.get("result", {})
    if result.get("isError"):
        logger.warning("spx_skew[%s]: isError response", as_of)
        return None
    content = result.get("content", [])
    raw = str(content[0].get("text", "")) if content and isinstance(content[0], dict) else ""
    try:
        payload = json.loads(raw)
        grid = payload[0]["surface"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        logger.warning("spx_skew[%s]: unparseable surface payload (%d chars)",
                       as_of, len(raw))
        return None

    header = [float(h) for h in grid[0][1:] if h is not None]
    expiries: list[tuple[pd.Timestamp, list[float]]] = []
    for row in grid[1:]:
        stamp = pd.Timestamp(row[0])
        ivs = [v for v in row[1:]]
        expiries.append((stamp, ivs))
    if not expiries:
        return None

    base = pd.Timestamp(as_of)
    target = base + pd.Timedelta(days=target_expiry_days)
    stamp, ivs = min(expiries, key=lambda e: abs((e[0] - target).days))

    def _iv_at(moneyness: float) -> float | None:
        best_idx = min(range(len(header)),
                       key=lambda i: abs(header[i] - moneyness))
        val = ivs[best_idx]  # ivs already excludes the expiry column
        return float(val) if val is not None else None

    low, high = _iv_at(moneyness_low), _iv_at(moneyness_high)
    if low is None or high is None:
        logger.warning("spx_skew[%s]: missing wing vols at %s/%s",
                       as_of, moneyness_low, moneyness_high)
        return None
    return round(low - high, 4)


class LsegSkewHistory:
    """Backfills the SPX skew series by replaying ``equity_vol_surface``
    with historical calculation dates (weekly cadence recommended)."""

    def __init__(self, mcp: MCPClient) -> None:
        self._mcp = mcp

    def fetch(self, dates: list[str], sleep_s: float = 0.3) -> pd.Series:
        import time
        out: dict[pd.Timestamp, float] = {}
        for d in dates:
            skew = spx_skew(self._mcp, d)
            if skew is not None:
                out[pd.Timestamp(d)] = skew
            time.sleep(sleep_s)
        series = pd.Series(out, name="lseg_spx_skew").sort_index()
        logger.info("lseg_spx_skew: %d obs (%s .. %s)", len(series),
                    series.index.min().date() if len(series) else "-",
                    series.index.max().date() if len(series) else "-")
        return series
