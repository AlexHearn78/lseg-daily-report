"""Shared settings: default calculation date and output paths.

Override via environment variables with the ``LSEG_`` prefix or a ``.env`` file.
LSEG credentials and the universe live in ``lseg_quant.mdu.config``.
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _latest_business_day(today: dt.date | None = None) -> dt.datetime:
    """Return the most recent weekday (Mon–Fri) at midnight UTC.

    Used as the default `as_of` date when the caller doesn't pass one explicitly.
    Replaces hardcoded literals like `dt.datetime(2026, 5, 10)`.
    """
    today = today or dt.date.today()
    # If today is Sat/Sun, walk back to Friday.
    while today.weekday() >= 5:
        today -= dt.timedelta(days=1)
    return dt.datetime(today.year, today.month, today.day)


class Settings(BaseSettings):
    """Application settings, loaded from env vars or .env."""

    model_config = SettingsConfigDict(
        env_prefix="LSEG_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Date defaults ---
    default_calculation_date: dt.datetime = _latest_business_day()

    # --- Paths ---
    repo_root: Path = Path(__file__).resolve().parents[2]

    @property
    def output_root(self) -> Path:
        return self.repo_root / "data" / "outputs"

    @property
    def reference_root(self) -> Path:
        return self.repo_root / "data" / "reference"


# Module-level singleton — import this, don't construct your own.
settings = Settings()
