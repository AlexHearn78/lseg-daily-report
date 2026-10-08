"""Small CSV-backed time-series store for regime indicator histories.

One CSV per series under ``data/raw/regime/history/<series>.csv`` with two
columns: ``date,value`` (ISO dates, floats). Sources do one-time backfills;
the daily job appends new observations. Upserts are idempotent per date.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from lseg_quant.config import settings

logger = logging.getLogger(__name__)

_DEFAULT_ROOT = settings.repo_root / "data" / "raw" / "regime" / "history"


class HistoryStore:
    """Read/write access to per-series CSV histories."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def path(self, series: str) -> Path:
        return self.root / f"{series}.csv"

    def exists(self, series: str) -> bool:
        return self.path(series).exists()

    def read(self, series: str) -> pd.Series:
        """Load a series as ``pd.Series`` indexed by date (ascending).

        Returns an empty series when nothing is stored yet.
        """
        path = self.path(series)
        if not path.exists():
            return pd.Series(dtype=float, name=series)
        df = pd.read_csv(path, parse_dates=["date"], index_col="date")
        values = df["value"].astype(float)
        values.name = series
        return values.sort_index()

    def upsert(self, series: str, data: pd.Series) -> int:
        """Merge *data* into the stored series (newest wins per date).

        Returns total row count after the merge.
        """
        data = data.dropna()
        if len(data):
            data.index = pd.DatetimeIndex(data.index)
        existing = self.read(series)
        parts = [s for s in (existing, data) if len(s)]
        if not parts:
            logger.warning("history[%s]: nothing to store", series)
            return 0
        combined = pd.concat(parts)
        if combined.index.has_duplicates:
            combined = combined[~combined.index.duplicated(keep="last")]
        combined = combined.sort_index()
        idx = pd.DatetimeIndex(combined.index)
        out = pd.DataFrame({"date": idx.strftime("%Y-%m-%d"),
                            "value": combined.values})
        out.to_csv(self.path(series), index=False)
        logger.info("history[%s]: %d rows (%s .. %s)", series, len(combined),
                    idx.min().date(), idx.max().date())
        return len(combined)

    def coverage(self) -> dict[str, dict[str, object]]:
        """Summary of every stored series: rows and date range."""
        summary: dict[str, dict[str, object]] = {}
        for path in sorted(self.root.glob("*.csv")):
            s = path.stem
            values = self.read(s)
            summary[s] = {
                "rows": len(values),
                "start": str(values.index.min()) if len(values) else None,
                "end": str(values.index.max()) if len(values) else None,
            }
        return summary
