"""FINRA margin statistics source — monthly debit balances since 1997.

FINRA publishes a single Excel file containing the full history. The site
sits behind bot protection; if the direct download fails, drop the file
manually at ``data/raw/regime/finra/margin-statistics.xlsx``.
"""
from __future__ import annotations

import datetime as dt
import io
import logging

import pandas as pd
import requests

from lseg_quant.config import settings

logger = logging.getLogger(__name__)

XLSX_URL = "https://www.finra.org/sites/default/files/margin-statistics.xlsx"
_LOCAL_DIR = settings.repo_root / "data" / "raw" / "regime" / "finra"
_LOCAL_FILE = _LOCAL_DIR / "margin-statistics.xlsx"


def fetch_xlsx_bytes(timeout: float = 60.0) -> bytes:
    """Download the FINRA workbook, falling back to a local manual copy."""
    try:
        resp = requests.get(
            XLSX_URL,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"},
        )
        if resp.ok and len(resp.content) > 10_000:
            logger.info("finra: downloaded %d bytes", len(resp.content))
            return resp.content
        logger.warning("finra: HTTP %s (%d bytes) from %s",
                       resp.status_code, len(resp.content), XLSX_URL)
    except requests.RequestException as exc:
        logger.warning("finra: download failed: %s", exc)

    if _LOCAL_FILE.exists():
        logger.info("finra: using manual file %s", _LOCAL_FILE)
        return _LOCAL_FILE.read_bytes()
    raise RuntimeError(
        f"FINRA xlsx unavailable ({XLSX_URL}) and no manual copy at {_LOCAL_FILE}. "
        "Download the 'Margin Statistics' Excel file manually and retry."
    )


def parse_margin_debt(xlsx_bytes: bytes) -> pd.Series:
    """Parse the workbook into a month-end ``debit_balances`` series ($m).

    Layout has shifted over the years; we locate the header row by scanning
    for a cell equal to 'Month' and pick columns by substring match.
    """
    raw = pd.read_excel(io.BytesIO(xlsx_bytes), header=None)
    header_row = None
    for idx, row in raw.iterrows():
        if any(str(c).strip().lower() == "month" for c in row):
            header_row = idx
            break
    if header_row is None:
        raise ValueError("FINRA xlsx: could not locate 'Month' header row")

    df = pd.DataFrame(raw.values[header_row + 1:], columns=[str(c).strip() if pd.notna(c) else "" for c in raw.iloc[header_row]])

    month_col = next(c for c in df.columns if c.lower().startswith("month"))
    debit_col = next((c for c in df.columns
                      if "debit" in c.lower() and "margin" in c.lower()),
                     None) or next(c for c in df.columns if "debit" in c.lower())

    parsed = pd.DataFrame({
        "raw_month": df[month_col].astype(str).str.strip(),
        "value": pd.to_numeric(df[debit_col], errors="coerce"),
    }).dropna()

    dates = parsed["raw_month"].map(_parse_month_label)
    out = pd.Series(parsed["value"].values, index=pd.DatetimeIndex(dates), name="margin_debit_balances")
    out = out[~out.index.isna()].dropna().sort_index()
    out.index = out.index.to_period("M").to_timestamp("M")
    logger.info("finra: %d monthly obs (%s .. %s)", len(out),
                out.index.min().date(), out.index.max().date())
    return out


def _parse_month_label(label: str):
    """Parse FINRA month labels like 'Jan-97', 'January 2026', '2026-01'."""
    label = label.strip()
    for fmt in ("%b-%y", "%b-%Y", "%B-%y", "%B-%Y", "%Y-%m"):
        try:
            return dt.datetime.strptime(label, fmt)
        except ValueError:
            continue
    return pd.NaT


def fetch_margin_debt() -> pd.Series:
    return parse_margin_debt(fetch_xlsx_bytes())


def excess_leverage(margin_debt: pd.Series, index_close: pd.Series) -> pd.Series:
    """Margin-debt YoY growth minus index YoY growth ('excess leverage').

    Cadence-generic: the benchmark series is aligned to the margin-debt
    observation dates by nearest-previous-value, so both monthly FINRA data
    and quarterly Z.1 data work. Only observations where both sides exist
    are returned.
    """
    md = margin_debt.dropna().sort_index()
    bench = index_close.dropna().sort_index()
    if not len(md) or not len(bench):
        return pd.Series(dtype=float, name="excess_leverage")

    def _yoy(s: pd.Series) -> pd.Series:
        """YoY % growth using each observation vs the closest prior value ~365d earlier."""
        values: dict[pd.Timestamp, float] = {}
        stamps = s.index
        locs = stamps.searchsorted(stamps - pd.Timedelta(days=365))
        for t, pos in zip(stamps, locs):
            if pos == 0:
                continue
            base = float(s.iloc[pos - 1])
            if base > 0:
                values[t] = (float(s.loc[t]) / base - 1.0) * 100.0
        return pd.Series(values, name=s.name)

    md_growth = _yoy(md)
    bench_growth = _yoy(bench)
    # Align benchmark growth onto margin-debt dates (previous value).
    bench_on_md = bench_growth.reindex(md_growth.index.union(bench_growth.index)).sort_index().reindex(md_growth.index, method="ffill")
    joined = pd.concat([md_growth.rename("md"), bench_on_md.rename("bench")], axis=1).dropna()
    out = (joined["md"] - joined["bench"]).rename("excess_leverage")
    logger.info("finra: excess_leverage %d obs (%s .. %s)", len(out),
                out.index.min().date() if len(out) else "-",
                out.index.max().date() if len(out) else "-")
    return out
