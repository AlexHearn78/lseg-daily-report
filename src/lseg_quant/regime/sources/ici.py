"""ICI weekly estimated long-term mutual fund flows (equity component).

ICI's long-history XLS is member-gated, so v1 scrapes the public weekly
release table on the flows page. History accumulates going forward and the
pillar stays ``low_confidence`` until ~3 years exist (handled by score.py).
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import re

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_FLOWS_PAGE = "https://www.ici.org/research/stats/flows"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def fetch_equity_flows(timeout: float = 60.0) -> pd.Series:
    """Weekly US equity fund flows ($m) from the ICI release page.

    The release table is laid out as: rows = categories ('Total equity',
    'Hybrid', 'Total bond', ...), columns = week-ending dates. We take the
    total-equity row across all date columns present.
    """
    resp = requests.get(_FLOWS_PAGE, headers=_HEADERS, timeout=timeout)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    for table in tables:
        series = _extract_equity_row(table)
        if series is not None and len(series):
            logger.info("ici: %d weekly obs (%s .. %s)", len(series),
                        series.index.min().date(), series.index.max().date())
            return series.sort_index()
    logger.warning("ici: no equity flow table found on %s", _FLOWS_PAGE)
    return pd.Series(dtype=float, name="ici_equity_flows")


def _extract_equity_row(table: pd.DataFrame) -> pd.Series | None:
    """Locate a total-equity row with parseable date columns."""
    if table.empty:
        return None
    first_col = table.columns[0]
    labels = table[first_col].astype(str).str.lower()
    mask = labels.str.contains("total equity") | labels.eq("equity")
    if not mask.any():
        return None
    row = table.loc[mask].iloc[0]

    out: dict[pd.Timestamp, float] = {}
    for col in table.columns[1:]:
        ts = _parse_week_label(str(col))
        if ts is None:
            continue
        try:
            value = float(row[col])
        except (TypeError, ValueError):
            continue
        out[ts] = value
    if not out:
        return None
    return pd.Series(out, name="ici_equity_flows")


def _parse_week_label(label: str) -> pd.Timestamp | None:
    """Parse ICI column labels like '2/11/2026' or '2026-02-11'."""
    label = label.strip()
    match = re.match(r"^(\d{1,2})/(\d{1,2})/(\d{4})$", label)
    if match:
        month, day, year = (int(g) for g in match.groups())
        return pd.Timestamp(year=year, month=month, day=day)
    for fmt in ("%Y-%m-%d", "%m/%d/%y"):
        try:
            return pd.Timestamp(dt.datetime.strptime(label, fmt))
        except ValueError:
            continue
    return None
