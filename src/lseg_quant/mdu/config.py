from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import logging
import os

logger = logging.getLogger(__name__)


# ------------------------------------------------------------------
# Ticker universe — loaded from config/universe.yaml
# ------------------------------------------------------------------

CONFIG_DIR = Path(os.environ.get("REPORT_CONFIG_DIR", Path(__file__).resolve().parents[3] / "config"))


@dataclass
class TickerInfo:
    """Configuration for a single instrument in the universe."""
    ric: str                         # LSEG RIC: prices, news, IBES and fundamentals
    ticker_display: str              # label shown in the report
    name: str = ""                   # company name (filled by resolve_universe.py)
    permid: int | None = None        # organisation PermID for the transcripts tool
    currency: str = "USD"            # quote currency as LSEG reports it, e.g. GBp
    benchmark_ric: str = ".SPX"
    group: Literal["holding", "watchlist"] = "holding"
    exchange: str = "XNYS"           # pandas-market-calendars exchange code

    @property
    def ticker(self) -> str:
        """RIC used for pricing (kept for callers written against the old field)."""
        return self.ric


def load_universe(config_dir: Path = CONFIG_DIR) -> dict[str, TickerInfo]:
    """Read ``universe.yaml`` and merge ``universe.resolved.json`` if present.

    The YAML holds what the client chooses (RIC, group, exchange). The resolved
    JSON holds what LSEG says about each RIC (name, organisation PermID, quote
    currency) and is written by ``workflows/resolve_universe.py``.
    """
    import json

    import yaml

    path = config_dir / "universe.yaml"
    if not path.exists():
        raise FileNotFoundError(f"{path} not found — the template ships one; edit it to list your names")
    doc = yaml.safe_load(path.read_text()) or {}
    default_benchmark = doc.get("benchmark_ric", ".SPX")
    resolved_path = config_dir / "universe.resolved.json"
    resolved = json.loads(resolved_path.read_text()) if resolved_path.exists() else {}

    universe: dict[str, TickerInfo] = {}
    for row in doc.get("names") or []:
        key = str(row["key"]).upper().strip()
        if key in universe:
            raise ValueError(f"Duplicate key {key} in {path}")
        extra = resolved.get(key, {})
        if extra and extra.get("ric") != row["ric"]:
            extra = {}  # RIC changed since the last resolve; ignore stale details
        universe[key] = TickerInfo(
            ric=row["ric"],
            ticker_display=row.get("display", key),
            name=row.get("name") or extra.get("name", key),
            permid=extra.get("permid"),
            currency=extra.get("currency") or row.get("currency", "USD"),
            benchmark_ric=row.get("benchmark_ric", default_benchmark),
            group=row.get("group", "holding"),
            exchange=row.get("exchange", "XNYS"),
        )
    if not universe:
        raise ValueError(f"{path} lists no names")
    return universe


def load_report_config(config_dir: Path = CONFIG_DIR) -> dict[str, Any]:
    """Read ``report.yaml`` (title, timezone). Missing file means defaults."""
    import yaml

    path = config_dir / "report.yaml"
    doc = (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}
    return {
        "title": doc.get("title", "Daily Report"),
        "timezone": doc.get("timezone", "Europe/London"),
        "timezone_label": doc.get("timezone_label", "London"),
    }


UNIVERSE: dict[str, TickerInfo] = load_universe()


_CALENDAR_CACHE: dict[str, "Any"] = {}


def is_trading_day(d: dt.date | dt.datetime, exchange: str = "XNYS") -> bool:
    """Check if *d* is a regular trading day on *exchange*.

    Uses ``pandas_market_calendars`` with a per-exchange cache.
    Returns ``True`` on weekends/holidays not in the exchange calendar.
    """
    try:
        import pandas_market_calendars as mcal
    except ImportError:
        return True  # no calendar lib — assume always open

    cal = _CALENDAR_CACHE.get(exchange)
    if cal is None:
        try:
            cal = mcal.get_calendar(exchange)
        except Exception:  # pandas-market-calendars raises various types for unknown names
            logger.warning("Unknown exchange calendar %r — treating every weekday as open", exchange)
            return d.weekday() < 5
        _CALENDAR_CACHE[exchange] = cal

    ds = d.isoformat() if isinstance(d, (dt.date, dt.datetime)) else str(d)
    days = cal.valid_days(start_date=ds, end_date=ds)
    return len(days) > 0


def lookup_ticker(symbol: str) -> TickerInfo:
    """Look up a ticker symbol in UNIVERSE. Raises KeyError if not found."""
    key = symbol.upper().strip()
    if key not in UNIVERSE:
        valid = ", ".join(sorted(UNIVERSE))
        raise KeyError(f"Unknown ticker '{symbol}'. Valid: {valid}")
    return UNIVERSE[key]


# ------------------------------------------------------------------
# MDU settings
# ------------------------------------------------------------------

@dataclass
class MDUSettings:
    # --- Instrument ---
    ticker: str = "MSFT.O"  # RIC for pricing
    ric: str = "MSFT.O"  # RIC for news
    ticker_display: str = "MSFT"  # display label
    currency: str = "USD"  # quote currency, used to check consensus currency
    benchmark_ric: str = ".SPX"

    # --- Scheduling ---
    calculation_date: dt.datetime = field(default_factory=lambda: dt.datetime.now())

    # --- LSEG MCP ---
    lseg_client_id: str = field(
        default_factory=lambda: os.environ.get("LSEG_CLIENT_ID", "")
    )
    lseg_client_secret: str = field(
        default_factory=lambda: os.environ.get("LSEG_CLIENT_SECRET", "")
    )
    lseg_token_url: str = "https://login.ciam.refinitiv.com/as/token.oauth2"
    lseg_mcp_endpoint: str = "https://api.analytics.lseg.com/lfa/mcp"

    # --- Paths ---
    run_dir: Path = field(default_factory=lambda: Path("data/outputs/mdu"))

    # --- Feature defaults ---
    lookback_years: int = 3
    news_lookback_days: int = 30

    # --- Portfolio sizing ---
    num_positions: int = 12  # number of holdings (not watchlist)

    # --- Risk constraints ---
    min_confidence: float = 0.75
    max_risk_off_allocation: float = 0.3
    max_drawdown_threshold: float = -0.15
    max_allocation: float = 1.0
