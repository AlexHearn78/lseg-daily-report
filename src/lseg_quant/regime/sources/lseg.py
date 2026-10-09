"""Market and macro series for the froth score and dashboard, all from LSEG.

Every input comes through the LSEG hosted MCP endpoint, so a client needs
no other data keys:

| Store name           | LSEG source                                          |
|----------------------|------------------------------------------------------|
| ``spx_close``        | ``.SPX`` daily close (historical_pricing_summaries)  |
| ``sofr``             | ``USDSOFR=`` New York Fed SOFR fixing                |
| ``effr``             | ``USONFFE=FEDR`` effective fed funds fixing          |
| ``eurex_putcall_sx5e`` | ``.PRSTXE.EX`` Euro Stoxx 50 put/call ratio (Eurex) |
| ``dgs2``, ``dgs10``  | YieldBook US government curve, 2y and 10y (fixed_income_curves) |
| ``cpi_yoy``          | ``USCONPRCE`` US CPI index, year-on-year (qa_macroeconomic) |

Derived: ``funding_spread`` (SOFR - EFFR), ``real_policy_rate`` (EFFR - CPI
YoY, each print used from its publication date) and ``spx_stretch`` (S&P 500
versus its 200-day average, %).
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any

import pandas as pd

from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.lseg_feeds import LsegMacroFeeds

logger = logging.getLogger(__name__)

PRICING_RICS: dict[str, str] = {
    "spx_close": ".SPX",
    "sofr": "USDSOFR=",
    "effr": "USONFFE=FEDR",
    "eurex_putcall_sx5e": ".PRSTXE.EX",
}
CPI_MNEMONIC = "USCONPRCE"
GVT_TERMS: dict[str, float] = {"dgs2": 2, "dgs10": 10}
GVT_BATCH = 20                 # fixed_income_curves takes up to 20 curve entries per call
CHUNK_DAYS = 730               # date range per pricing call, to keep responses small
CPI_PUBLICATION_LAG_DAYS = 45  # CPI for month M is published about two weeks into M+1
STRETCH_WINDOW = 200           # trading days in the S&P 500 moving average
HISTORY_YEARS = 10             # backfill depth when a series is empty
OVERLAP_DAYS = 10              # refetch window on incremental runs, to pick up revisions


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------

def parse_pricing(payload: list[dict[str, Any]]) -> pd.Series:
    """Daily values from a historical_pricing_summaries response.

    Uses the response's own ``defaultPricingField`` (TRDPRC_1 for an index,
    FIXING_1 for a rate fixing, PUTCAL_RTO for a put/call ratio).
    """
    points: dict[pd.Timestamp, float] = {}
    for item in payload:
        field = item.get("defaultPricingField")
        headers = [h["name"] for h in item.get("headers", [])]
        if not field or field not in headers or "DATE" not in headers:
            continue
        i_date, i_val = headers.index("DATE"), headers.index(field)
        for row in item.get("data", []):
            value = row[i_val]
            if value is not None:
                points[pd.Timestamp(row[i_date])] = float(value)
    return pd.Series(points, dtype=float).sort_index()


def pricing_series(mcp: MCPClient, ric: str, start: dt.date, end: dt.date) -> pd.Series:
    """Daily history for one RIC, fetched in date-range chunks."""
    parts: list[pd.Series] = []
    cursor = start
    while cursor <= end:
        stop = min(cursor + dt.timedelta(days=CHUNK_DAYS), end)
        resp = mcp.call_tool("historical_pricing_summaries", {
            "universe": ric, "interval": "P1D",
            "start": cursor.isoformat(), "end": stop.isoformat()})
        if resp.get("result", {}).get("isError"):
            raise RuntimeError(f"{ric}: {MCPClient._extract_text(resp)[:200]}")
        text = MCPClient._extract_text(resp)
        payload = json.loads(text) if text else {}
        rows = payload.get("data", []) if isinstance(payload, dict) else payload
        parts.append(parse_pricing(rows))
        cursor = stop + dt.timedelta(days=1)
    out = pd.concat(parts) if parts else pd.Series(dtype=float)
    return out[~out.index.duplicated(keep="last")].sort_index()


def parse_gvt(payload: dict[str, Any] | list[Any]) -> dict[str, dict[pd.Timestamp, float]]:
    """{store name: {date: yield %}} from a fixed_income_curves response.

    Accepts ``{"results": [...]}`` or a bare list of curves.
    """
    by_term = {term: name for name, term in GVT_TERMS.items()}
    out: dict[str, dict[pd.Timestamp, float]] = {name: {} for name in GVT_TERMS}
    curves = payload.get("results", []) if isinstance(payload, dict) else payload
    for curve in curves:
        date = curve.get("pricingDate")
        points = curve.get("points") or {}
        cols = points.get("columns", [])
        if not date or "rate" not in cols or "term" not in cols:
            continue
        i_rate, i_term = cols.index("rate"), cols.index("term")
        for row in points.get("rows", []):
            name = by_term.get(float(row[i_term]))
            if name and row[i_rate] is not None:
                out[name][pd.Timestamp(date)] = float(row[i_rate])
    return out


def gvt_yields(mcp: MCPClient, dates: list[dt.date]) -> dict[str, pd.Series]:
    """US Treasury 2y and 10y yields (%) on each date, from the YieldBook curve."""
    found: dict[str, dict[pd.Timestamp, float]] = {name: {} for name in GVT_TERMS}
    for i in range(0, len(dates), GVT_BATCH):
        curves = [{"currency": "USD", "curveType": "GVT", "curveId": f"UST_{d:%Y%m%d}",
                   "pricingDate": d.isoformat(), "terms": sorted(GVT_TERMS.values())}
                  for d in dates[i:i + GVT_BATCH]]
        resp = mcp.call_tool("fixed_income_curves", {"curves": curves})
        text = MCPClient._extract_text(resp)
        if resp.get("result", {}).get("isError") or not text:
            logger.warning("fixed_income_curves failed for %s..", dates[i])
            continue
        for name, points in parse_gvt(json.loads(text)).items():
            found[name].update(points)
    return {name: pd.Series(points, dtype=float).sort_index() for name, points in found.items()}


def cpi_yoy(mcp: MCPClient, start: dt.date) -> pd.Series:
    """US CPI year-on-year (%), monthly, dated to the month it measures."""
    index = LsegMacroFeeds(mcp).series(CPI_MNEMONIC, from_date=(start - dt.timedelta(days=400)).isoformat())
    if not len(index):
        return pd.Series(dtype=float, name="cpi_yoy")
    monthly = index.groupby(index.index.to_period("M")).last()
    monthly.index = monthly.index.to_timestamp()
    return (monthly.pct_change(12, fill_method=None) * 100).dropna().rename("cpi_yoy")


# ---------------------------------------------------------------------------
# Derived metrics
# ---------------------------------------------------------------------------

def derived_metrics(series: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Froth inputs computed from the raw LSEG series."""
    out: dict[str, pd.Series] = {}
    sofr, effr = series.get("sofr"), series.get("effr")
    if sofr is not None and effr is not None and len(sofr) and len(effr):
        joined = pd.concat([sofr.rename("sofr"), effr.rename("effr")], axis=1).dropna()
        out["funding_spread"] = (joined["sofr"] - joined["effr"]).rename("funding_spread")

    cpi = series.get("cpi_yoy")
    if effr is not None and cpi is not None and len(effr) and len(cpi):
        known = cpi.copy()
        known.index = (known.index.to_period("M").to_timestamp()
                       + pd.Timedelta(days=CPI_PUBLICATION_LAG_DAYS))
        aligned = pd.concat([effr.rename("effr"), known.rename("cpi")], axis=1).sort_index()
        aligned["cpi"] = aligned["cpi"].ffill()
        aligned = aligned.dropna(subset=["effr", "cpi"])
        out["real_policy_rate"] = (aligned["effr"] - aligned["cpi"]).rename("real_policy_rate")

    spx = series.get("spx_close")
    if spx is not None and len(spx) > STRETCH_WINDOW:
        avg = spx.rolling(STRETCH_WINDOW).mean()
        out["spx_stretch"] = ((spx / avg - 1) * 100).dropna().rename("spx_stretch")
    return out


# ---------------------------------------------------------------------------
# Refresh
# ---------------------------------------------------------------------------

def _start_for(store: HistoryStore, name: str, today: dt.date) -> dt.date:
    hist = store.read(name)
    if len(hist):
        return (hist.index.max() - pd.Timedelta(days=OVERLAP_DAYS)).date()
    return today - dt.timedelta(days=365 * HISTORY_YEARS)


def _gvt_dates(start: dt.date, today: dt.date) -> list[dt.date]:
    """Weekly (Friday) dates for a backfill, every business day for the last few weeks."""
    weekly = [d.date() for d in pd.date_range(start, today, freq="W-FRI")]
    recent = [d.date() for d in pd.bdate_range(max(start, today - dt.timedelta(days=35)), today)]
    return sorted(set(weekly) | set(recent))


def refresh(store: HistoryStore, mcp: MCPClient, today: dt.date | None = None) -> dict[str, str]:
    """Update every LSEG series in the store; returns {series: error} for failures.

    An empty series is backfilled ten years (the Treasury yields weekly);
    otherwise only the days since the last observation are fetched. Each
    source is isolated so one failure does not stop the others.
    """
    today = today or dt.date.today()
    errors: dict[str, str] = {}
    for name, ric in PRICING_RICS.items():
        try:
            store.upsert(name, pricing_series(mcp, ric, _start_for(store, name, today), today).rename(name))
        except Exception as exc:  # noqa: BLE001 - source isolation
            errors[name] = str(exc)
            logger.warning("lseg %s (%s) failed: %s", name, ric, exc)
    try:
        start = min(_start_for(store, n, today) for n in GVT_TERMS)
        for name, s in gvt_yields(mcp, _gvt_dates(start, today)).items():
            if len(s):
                store.upsert(name, s.rename(name))
    except Exception as exc:  # noqa: BLE001
        errors["gvt"] = str(exc)
        logger.warning("lseg treasury yields failed: %s", exc)
    try:
        cpi = cpi_yoy(mcp, _start_for(store, "cpi_yoy", today))
        if len(cpi):
            store.upsert("cpi_yoy", cpi)
    except Exception as exc:  # noqa: BLE001
        errors["cpi_yoy"] = str(exc)
        logger.warning("lseg CPI failed: %s", exc)

    raw = {n: store.read(n) for n in (*PRICING_RICS, "cpi_yoy")}
    for name, series in derived_metrics(raw).items():
        store.upsert(name, series)
    return errors
