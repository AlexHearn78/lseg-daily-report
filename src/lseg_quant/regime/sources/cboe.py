"""CBOE daily market statistics — total equity put/call ratio.

The historical daily CSVs live on CBOE's CDN with a per-year path. The URL
pattern has moved before, so we probe a small set of candidate layouts per
date and remember the first that works for the rest of the run.
"""
from __future__ import annotations

import datetime as dt
import io
import logging
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_CANDIDATE_TEMPLATES = [
    "https://cdn.cboe.com/api/global/daily_market_statistics/historical/{year}/{date}.csv",
]

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

# Monthly volume-archive workbooks (not behind the Akamai wall that blocks
# the daily statistics CDN path).
_ARCHIVE_TEMPLATE = ("https://cdn.cboe.com/resources/options/volume_archive/"
                     "{year}/{year}_{month:02d}_rank_wosym.xlsx")


def fetch_monthly_archive(year: int, month: int,
                          timeout: float = 60.0) -> tuple[pd.Timestamp, float] | None:
    """Market-wide put/call volume ratio for one month, or None.

    Sums the Call/Put total-volume columns across every listed symbol in
    the archive workbook (~2900 rows/month since 2019).
    """
    url = _ARCHIVE_TEMPLATE.format(year=year, month=month)
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=timeout)
        if resp.status_code != 200 or len(resp.content) < 10_000:
            return None
        df = pd.read_excel(io.BytesIO(resp.content))
    except Exception as exc:  # noqa: BLE001 - defensive against layout drift
        logger.debug("cboe: archive %04d-%02d failed: %s", year, month, exc)
        return None

    cols = {str(c).strip().lower(): c for c in df.columns}
    call_col = next((cols[c] for c in cols if c == "call"), None)
    put_col = next((cols[c] for c in cols if c == "put"), None)
    if call_col is None or put_col is None:
        return None
    calls = pd.to_numeric(df[call_col], errors="coerce").sum()
    puts = pd.to_numeric(df[put_col], errors="coerce").sum()
    if calls <= 0:
        return None
    month_end = pd.Timestamp(year=year, month=month, day=1) + pd.offsets.MonthEnd(0)
    return month_end, round(float(puts / calls), 4)


def fetch_monthly_history(start_year: int = 2019, end: dt.date | None = None,
                          sleep_s: float = 0.5) -> pd.Series:
    """Monthly put/call ratios from every available archive workbook."""
    end = end or dt.date.today()
    out: dict[pd.Timestamp, float] = {}
    year, month = start_year, 1
    while (year, month) <= (end.year, end.month):
        got = fetch_monthly_archive(year, month)
        if got:
            ts, ratio = got
            out[ts] = ratio
        else:
            logger.debug("cboe: no archive for %04d-%02d", year, month)
        month += 1
        if month > 12:
            year, month = year + 1, 1
        if sleep_s:
            time.sleep(sleep_s)
    series = pd.Series(out, name="cboe_putcall").sort_index()
    logger.info("cboe: %d monthly obs (%s .. %s)", len(series),
                series.index.min() if len(series) else "-",
                series.index.max() if len(series) else "-")
    return series


def _candidate_urls(day: dt.date) -> list[str]:
    iso = day.isoformat()
    return [t.format(year=day.year, date=iso) for t in _CANDIDATE_TEMPLATES]


def fetch_day(day: dt.date, timeout: float = 30.0) -> float | None:
    """Total put/call volume ratio for one trading day, or None."""
    for url in _candidate_urls(day):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=timeout)
            if resp.status_code != 200 or len(resp.content) < 100:
                continue
            ratio = _extract_total_ratio(resp.content)
            if ratio is not None:
                return ratio
        except requests.RequestException as exc:
            logger.debug("cboe: %s failed: %s", url, exc)
            continue
    return None


def _extract_total_ratio(csv_bytes: bytes) -> float | None:
    """Find the total put/call ratio inside a daily statistics CSV."""
    try:
        df = pd.read_csv(io.BytesIO(csv_bytes))
    except Exception as exc:  # noqa: BLE001 - defensive against layout drift
        logger.debug("cboe: csv parse failed: %s", exc)
        return None

    df.columns = [str(c).strip().lower() for c in df.columns]
    ratio_cols = [c for c in df.columns if "put/call" in c or "putcall" in c]
    if not ratio_cols:
        return None
    ratio_col = ratio_cols[0]
    first_col = df.columns[0]

    # Prefer the consolidated 'total' row; fall back to mean across classes.
    labels = df[first_col].astype(str).str.lower()
    total_mask = labels.str.contains("total") & labels.str.contains("option")
    subset = df.loc[total_mask] if total_mask.any() else df
    values = pd.to_numeric(subset[ratio_col], errors="coerce").dropna()
    if values.empty:
        return None
    return round(float(values.iloc[0]), 4)


def fetch_range(start: dt.date, end: dt.date,
                sleep_s: float = 0.2) -> pd.Series:
    """Fetch put/call ratios for each weekday between *start* and *end*."""
    out: dict[pd.Timestamp, float] = {}
    day = start
    consecutive_misses = 0
    while day <= end:
        if day.weekday() < 5:
            ratio = fetch_day(day)
            if ratio is not None:
                out[pd.Timestamp(day)] = ratio
                consecutive_misses = 0
            else:
                consecutive_misses += 1
                if consecutive_misses >= 10 and not out:
                    logger.error("cboe: %d initial misses — endpoint pattern "
                                 "likely changed; aborting range fetch",
                                 consecutive_misses)
                    break
        day += dt.timedelta(days=1)
        if sleep_s:
            time.sleep(sleep_s)

    series = pd.Series(out, name="cboe_putcall").sort_index()
    logger.info("cboe: %d obs (%s .. %s)", len(series),
                series.index.min() if len(series) else "-",
                series.index.max() if len(series) else "-")
    return series
