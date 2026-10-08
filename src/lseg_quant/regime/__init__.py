"""Market regime & froth score — market-wide risk context signals."""
from __future__ import annotations

from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.score import (
    MetricInput,
    band_for,
    compute_froth_score,
    historical_composite,
    historical_percentiles,
    minmax_score,
    percentile_score,
    velocity_flag,
)

__all__ = [
    "HistoryStore",
    "MetricInput",
    "band_for",
    "compute_froth_score",
    "historical_composite",
    "historical_percentiles",
    "minmax_score",
    "percentile_score",
    "velocity_flag",
]
