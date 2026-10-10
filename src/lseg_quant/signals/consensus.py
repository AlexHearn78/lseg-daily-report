"""Daily IBES consensus snapshots, so post-results analysis can use the
consensus that stood *before* an announcement.

``qa_ibes_consensus`` returns the latest published consensus only: its
"historical" periods are past fiscal periods, not past dates. So the
pipeline keeps its own point-in-time history, one CSV per ticker under
``data/raw/consensus/``:

    snapshot_date, kind, period_type, period_end, measure, value, announce_utc

``kind`` is ``estimate`` (one row per snapshot date, fiscal period and
measure) or ``actual`` (stored once per fiscal period and measure, with the
IBES announcement time).
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

from lseg_quant.mdu.config import UNIVERSE

logger = logging.getLogger(__name__)

STORE_DIR = Path("data/raw/consensus")
FIELDS = ["snapshot_date", "kind", "period_type", "period_end", "measure", "value", "announce_utc"]
MEASURE_KEYS = {"epsPerShare": "EPS", "revMillion": "REV"}


def _date(value: Any) -> str:
    """IBES 'MM/DD/YYYY ...' or ISO text to 'YYYY-MM-DD'."""
    text = str(value or "")
    if "/" in text[:10]:
        return dt.datetime.strptime(text[:10], "%m/%d/%Y").date().isoformat()
    return text[:10]


def _utc(value: Any) -> str:
    """IBES 'MM/DD/YYYY HH:MM:SS' (UTC) to ISO 8601 with offset; '' when absent."""
    try:
        return dt.datetime.strptime(str(value), "%m/%d/%Y %H:%M:%S").replace(
            tzinfo=dt.timezone.utc).isoformat()
    except ValueError:
        return ""


def fetch_snapshot(mcp: Any, key: str, today: dt.date) -> list[dict]:
    """Latest quarterly actuals plus next-quarter and FY1/FY2 consensus, as store rows."""
    ticker, ticker_type = UNIVERSE[key].ibes_id  # RIC unless the universe overrides it
    resp = mcp.call_tool("qa_ibes_consensus", {"requests": [
        {"dataType": "qa_ibes_actuals", "options": {
            "ticker": ticker, "tickerType": ticker_type, "measures": ["Eps", "Rev"], "periodType": "Quarter", "pIndex": 0}},
        {"dataType": "qa_ibes_consensus", "options": {
            "ticker": ticker, "tickerType": ticker_type, "measures": ["Eps", "Rev"], "periodType": "Quarter",
            "periodIndexStart": 0, "periodIndexEnd": 1}},
        {"dataType": "qa_ibes_consensus", "options": {
            "ticker": ticker, "tickerType": ticker_type, "measures": ["Eps", "Rev"], "periodType": "Year",
            "periodIndexStart": 1, "periodIndexEnd": 2}},
    ]})
    result = resp.get("result", {})
    if result.get("isError"):
        raise RuntimeError("qa_ibes_consensus returned an error")
    content = result.get("content", [])
    data = json.loads(content[0]["text"]) if content else []
    day = today.isoformat()
    rows: list[dict] = []
    for entry in data if isinstance(data, list) else []:
        payload = (entry.get("response") or {}).get("data") or {}
        period_type = (entry.get("options") or {}).get("periodType", "")
        for a in payload.get("actuals") or []:
            rows.append({"snapshot_date": day, "kind": "actual", "period_type": "Quarter",
                         "period_end": _date(a.get("date")),
                         "measure": str(a.get("measureCode", "")).upper(),
                         "value": a.get("normActValue"), "announce_utc": _utc(a.get("announceDateUTC"))})
        for est in payload.get("estimates") or []:
            for name, measure in (est.get("measures") or {}).items():
                if isinstance(measure, dict) and measure.get("value") is not None:
                    rows.append({"snapshot_date": day, "kind": "estimate", "period_type": period_type,
                                 "period_end": _date(est.get("date")),
                                 "measure": MEASURE_KEYS.get(name, name.upper()),
                                 "value": measure["value"], "announce_utc": ""})
    return rows


def _identity(row: dict) -> tuple:
    if row["kind"] == "actual":  # an actual is stored once, whatever day we saw it
        return ("actual", row["period_end"], row["measure"])
    return ("estimate", row["snapshot_date"], row["period_type"], row["period_end"], row["measure"])


def load(store_dir: Path, key: str) -> list[dict]:
    path = store_dir / f"{key}.csv"
    if not path.is_file():
        return []
    with path.open(newline="") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row["value"] = float(row["value"]) if row.get("value") not in (None, "") else None
    return rows


def append(store_dir: Path, key: str, rows: list[dict]) -> int:
    """Add rows not already stored; returns how many were added."""
    seen = {_identity(r) for r in load(store_dir, key)}
    new = [r for r in rows if _identity(r) not in seen]
    if not new:
        return 0
    store_dir.mkdir(parents=True, exist_ok=True)
    path = store_dir / f"{key}.csv"
    write_header = not path.is_file()
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        if write_header:
            writer.writeheader()
        writer.writerows({k: r.get(k, "") for k in FIELDS} for r in new)
    return len(new)
