"""CFTC Commitments of Traders source — net speculative positioning.

Uses the Socrata open-data API on publicreporting.cftc.gov (Legacy Futures
Only dataset ``6dca-aqww``). We track the S&P 500 Consolidated contract's
non-commercial net position (classic spec-positioning proxy).
"""
from __future__ import annotations

import logging
import time

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_DATASET_URL = "https://publicreporting.cftc.gov/resource/6dca-aqww.json"
_MARKET_FILTER = "upper(market_and_exchange_names) like '%S&P 500%CONSOLIDATED%'"


def fetch_net_spec_positioning(start: str | None = None,
                               timeout: float = 120.0) -> pd.Series:
    """Return the non-commercial net position series (contracts).

    ``start`` filters to reports on/after that date (YYYY-MM-DD). Full
    history back to 1986 is available; the API caps single responses at
    50k rows, so we page until exhausted.
    """
    frames: list[pd.DataFrame] = []
    offset = 0
    page_size = 50000

    while True:
        params: dict[str, str] = {
            "$select": "report_date_as_yyyy_mm_dd,market_and_exchange_names,noncomm_positions_long_all,noncomm_positions_short_all",
            "$where": _MARKET_FILTER,
            "$order": "report_date_as_yyyy_mm_dd ASC",
            "$limit": str(page_size),
            "$offset": str(offset),
        }
        if start:
            params["$where"] = (
                f"report_date_as_yyyy_mm_dd >= '{start}' AND {_MARKET_FILTER}"
            )
        resp = requests.get(_DATASET_URL, params=params, timeout=timeout,
                            headers={"User-Agent": "regime-froth/1.0"})
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(pd.DataFrame(rows))
        offset += page_size
        if len(rows) < page_size:
            break
        time.sleep(1.0)  # Socrata throttling courtesy

    if not frames:
        logger.warning("cftc: no rows returned for filter %s", _MARKET_FILTER)
        return pd.Series(dtype=float, name="cftc_net_spec")

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset=["report_date_as_yyyy_mm_dd"])
    dates = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    longs = pd.to_numeric(df["noncomm_positions_long_all"], errors="coerce")
    shorts = pd.to_numeric(df["noncomm_positions_short_all"], errors="coerce")
    net = pd.Series((longs - shorts).values, index=pd.DatetimeIndex(dates),
                    name="cftc_net_spec").dropna().sort_index()
    logger.info("cftc: %d weekly obs (%s .. %s)", len(net),
                net.index.min().date(), net.index.max().date())
    return net
