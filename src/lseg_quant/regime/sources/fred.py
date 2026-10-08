"""FRED REST API source — rates, inflation, breakevens, credit spreads.

Free API key required (``FRED_API_KEY``). Full history is returned in a
single paginated-safe call per series, which doubles as the backfill.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd
import requests

logger = logging.getLogger(__name__)

_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

CPI_PUBLICATION_LAG_DAYS = 45

# key -> (FRED series id, extra params). CPI uses YoY percent-change units.
SERIES: dict[str, tuple[str, dict[str, str]]] = {
    "sofr": ("SOFR", {}),
    "effr": ("EFFR", {}),
    "fed_funds_upper": ("DFEDTARU", {}),
    "cpi_yoy": ("CPIAUCSL", {"units": "pc1"}),
    "breakeven_10y": ("T10YIE", {}),
    "hy_oas": ("BAMLH0A0HYM2", {}),
    "spx": ("SP500", {}),  # daily close, trailing ~10y only
    # Z.1 quarterly margin loans at brokers & dealers — programmatic
    # fallback when FINRA's bot-protected xlsx is unavailable.
    "margin_loans_z1": ("BOGZ1FL663067003Q", {}),
    "dgs2": ("DGS2", {}),
    "dgs10": ("DGS10", {}),
}


def api_key() -> str:
    return os.environ.get("FRED_API_KEY", "")


def fetch_series(key: str, start: str | None = None,
                 api_key_value: str | None = None) -> pd.Series:
    """Fetch one FRED series as a datetime-indexed float series."""
    if key not in SERIES:
        raise KeyError(f"Unknown FRED series key '{key}'. Valid: {sorted(SERIES)}")
    key_id, extra = SERIES[key]
    params: dict[str, Any] = {
        "series_id": key_id,
        "api_key": api_key_value or api_key(),
        "file_type": "json",
        "limit": 100000,
        **extra,
    }
    if start:
        params["observation_start"] = start
    resp = requests.get(_OBSERVATIONS_URL, params=params, timeout=60)
    resp.raise_for_status()
    observations = resp.json().get("observations", [])
    dates = [pd.Timestamp(o["date"]) for o in observations]
    values: list[float] = []
    for o in observations:
        try:
            values.append(float(o["value"]))
        except (TypeError, ValueError):
            values.append(float("nan"))
    s = pd.Series(values, index=pd.DatetimeIndex(dates), name=key).dropna()
    logger.info("fred[%s]: %d obs (%s .. %s)", key, len(s),
                s.index.min().date() if len(s) else "-",
                s.index.max().date() if len(s) else "-")
    return s


def fetch_all(start: str | None = None) -> dict[str, pd.Series]:
    """Fetch every configured series. Missing API key returns empty dict."""
    if not api_key():
        logger.warning("FRED_API_KEY not set — skipping all FRED series")
        return {}
    out: dict[str, pd.Series] = {}
    for key in SERIES:
        try:
            out[key] = fetch_series(key, start=start)
        except requests.RequestException as exc:
            logger.error("fred[%s] failed: %s", key, exc)
    return out


def derived_metrics(series_map: dict[str, pd.Series]) -> dict[str, pd.Series]:
    """Compute derived froth inputs from raw FRED series.

    - ``funding_spread``  : SOFR - EFFR (daily; wide = stress)
    - ``real_policy_rate``: Fed funds upper target - CPI YoY (monthly step,
      each print used only from its publication date, forward-filled)
    """
    out: dict[str, pd.Series] = {}

    sofr = series_map.get("sofr")
    effr = series_map.get("effr")
    if sofr is not None and effr is not None and len(sofr) and len(effr):
        joined = pd.concat([sofr.rename("sofr"), effr.rename("effr")], axis=1).dropna()
        out["funding_spread"] = (joined["sofr"] - joined["effr"]).rename("funding_spread")

    ff = series_map.get("fed_funds_upper")
    cpi = series_map.get("cpi_yoy")
    if ff is not None and cpi is not None and len(ff) and len(cpi):
        # CPI YoY for month M is published ~2 weeks into M+1, so each print
        # only becomes usable 45 days after the month it is dated to.
        cpi_known = cpi.copy()
        cpi_known.index = (cpi_known.index.to_period("M").to_timestamp()
                           + pd.Timedelta(days=CPI_PUBLICATION_LAG_DAYS))
        daily_cpi = cpi_known.resample("D").last().ffill()
        aligned = pd.concat([ff.rename("ff"), daily_cpi.rename("cpi")], axis=1)
        aligned["cpi"] = aligned["cpi"].ffill()  # carry the last print to today
        aligned = aligned.dropna()
        out["real_policy_rate"] = (aligned["ff"] - aligned["cpi"]).rename("real_policy_rate")

    return out
