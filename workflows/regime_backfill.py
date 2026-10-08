#!/usr/bin/env python3
"""One-time full-history backfill for the regime froth score sources.

Pulls complete archives from every configured source into the CSV history
store at ``data/raw/regime/history/`` and writes a manifest with coverage +
a reconstructed historical composite preview.

Usage:
    uv run workflows/regime_backfill.py                # everything
    uv run workflows/regime_backfill.py --only fred,cftc
    uv run workflows/regime_backfill.py --cboe-days 120 --lseg
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv

load_dotenv(".env")

from lseg_quant.config import settings
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.regime.history import HistoryStore
from lseg_quant.regime.lseg_feeds import LsegSkewHistory
from lseg_quant.regime.score import historical_composite
from lseg_quant.regime.sources import cboe, cftc, fred, finra, ici

logger = logging.getLogger("regime_backfill")

# Froth-score metric wiring: metric name -> (pillar, invert)
PILLAR_OF: dict[str, str] = {
    "hy_oas": "valuation",
    "margin_debit_balances": "leverage",
    "margin_loans_z1": "leverage",
    "excess_leverage": "leverage",
    "cftc_net_spec": "positioning",
    "lseg_spx_skew": "positioning",
    "cboe_putcall_monthly": "positioning",
    "cboe_putcall": "positioning",
    "ici_equity_flows": "positioning",
    "funding_spread": "liquidity",
    "real_policy_rate": "liquidity",
}
INVERT_OF: dict[str, bool] = {
    "hy_oas": True,
    "cboe_putcall": True,
    "cboe_putcall_monthly": True,
    "lseg_spx_skew": True,
    "funding_spread": True,
    "real_policy_rate": True,
}


def backfill_fred(store: HistoryStore) -> None:
    raw = fred.fetch_all()
    for name, series in raw.items():
        store.upsert(name, series)
    for name, series in fred.derived_metrics(raw).items():
        store.upsert(name, series)


def backfill_spx(store: HistoryStore) -> None:
    try:
        spx = fred.fetch_series("spx")
        store.upsert("spx_close", spx)
    except Exception as exc:  # noqa: BLE001 - source isolation
        logger.error("SPX history failed: %s", exc)


def backfill_finra(store: HistoryStore) -> None:
    """Margin debt: FINRA monthly xlsx if reachable, else FRED Z.1 quarterly.

    Excess leverage = margin YoY growth minus SPX YoY growth, computed from
    whichever margin series is available.
    """
    try:
        debt = finra.fetch_margin_debt()
        store.upsert("margin_debit_balances", debt)
    except Exception as exc:  # noqa: BLE001 - bot protection / missing file
        logger.warning("FINRA xlsx unavailable (%s) — falling back to Z.1", exc)

    if not store.exists("margin_debit_balances"):
        z1 = fred.fetch_series("margin_loans_z1")
        store.upsert("margin_loans_z1", z1)
        margin = z1
    else:
        margin = store.read("margin_debit_balances")

    spx = store.read("spx_close")
    if len(spx):
        store.upsert("excess_leverage", finra.excess_leverage(margin, spx))
    else:
        logger.warning("leverage: no SPX history — excess_leverage skipped")


def backfill_cftc(store: HistoryStore) -> None:
    try:
        store.upsert("cftc_net_spec", cftc.fetch_net_spec_positioning())
    except Exception as exc:  # noqa: BLE001
        logger.error("CFTC backfill failed: %s", exc)


def backfill_cboe(store: HistoryStore, lookback_days: int) -> None:
    """Monthly archive workbooks (primary history) + optional daily seeding."""
    try:
        store.upsert("cboe_putcall_monthly", cboe.fetch_monthly_history())
    except Exception as exc:  # noqa: BLE001
        logger.error("CBOE monthly archives failed: %s", exc)
    end = dt.date.today()
    start = end - dt.timedelta(days=lookback_days)
    try:
        store.upsert("cboe_putcall", cboe.fetch_range(start, end))
    except Exception as exc:  # noqa: BLE001 - expected while CDN is blocked
        logger.info("CBOE daily stats unavailable (expected): %s", exc)


def backfill_lseg_skew(store: HistoryStore, years: int) -> None:
    """Weekly SPX skew points replayed from historical calculation dates."""
    end = dt.date.today()
    start = end - dt.timedelta(weeks=int(years * 52))
    dates = [str(d.date()) for d in
             pd.date_range(start, end, freq="W-WED") if d.date() <= end]
    logger.info("[backfill] LSEG SPX skew (%d weekly points)", len(dates))
    try:
        with MCPClient(client_id=os.environ["LSEG_CLIENT_ID"],
                       client_secret=os.environ["LSEG_CLIENT_SECRET"]) as mcp:
            series = LsegSkewHistory(mcp).fetch(dates)
        store.upsert("lseg_spx_skew", series)
    except Exception as exc:  # noqa: BLE001
        logger.error("LSEG skew backfill failed: %s", exc)


def backfill_ici(store: HistoryStore) -> None:
    try:
        store.upsert("ici_equity_flows", ici.fetch_equity_flows())
    except Exception as exc:  # noqa: BLE001
        logger.error("ICI backfill failed: %s", exc)


def preview_composite(store: HistoryStore) -> dict[str, object]:
    """Reconstruct the historical composite from whatever history exists."""
    histories: dict[str, pd.Series] = {}
    for name in PILLAR_OF:
        s = store.read(name)
        if len(s):
            histories[name] = s
    if not histories:
        return {}
    composite = historical_composite(histories, PILLAR_OF, INVERT_OF)
    return {
        "observations": len(composite),
        "start": str(composite.index.min()) if len(composite) else None,
        "end": str(composite.index.max()) if len(composite) else None,
        "latest_score": round(float(composite.iloc[-1]), 1) if len(composite) else None,
        "series_csv": _dump_composite(composite),
    }


def _dump_composite(composite: pd.Series) -> str | None:
    if not len(composite):
        return None
    out_dir = settings.output_root / "regime_backfill" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "historical_composite.csv"
    pd.DataFrame({"date": composite.index.strftime("%Y-%m-%d"),
                  "value": composite.round(2).values}).to_csv(path, index=False)
    logger.info("Historical composite written to %s", path)
    return str(path)


SOURCES = {
    "fred": lambda s: backfill_fred(s),
    "spx": lambda s: backfill_spx(s),
    "finra": lambda s: backfill_finra(s),
    "cftc": lambda s: backfill_cftc(s),
    "cboe": None,  # handled specially (needs --cboe-days)
    "ici": lambda s: backfill_ici(s),
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regime froth score backfill")
    parser.add_argument("--only", type=str, default=None,
                        help="Comma-separated subset: fred,spx,finra,cftc,cboe,ici,lseg_skew")
    parser.add_argument("--cboe-days", type=int, default=60,
                        help="Business-day lookback for CBOE daily put/call seeding (default 60)")
    parser.add_argument("--skew-years", type=int, default=3,
                        help="Years of weekly SPX skew history to replay via LSEG (default 3)")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stderr)

    all_sources = {"fred", "spx", "finra", "cftc", "cboe", "ici", "lseg_skew"}
    selected = set(args.only.split(",")) if args.only else all_sources
    store = HistoryStore()

    if "fred" in selected:
        logger.info("[backfill] FRED")
        SOURCES["fred"](store)
    if "spx" in selected:
        logger.info("[backfill] SPX (via FRED)")
        SOURCES["spx"](store)
    if "finra" in selected:
        logger.info("[backfill] FINRA margin statistics")
        SOURCES["finra"](store)
    if "cftc" in selected:
        logger.info("[backfill] CFTC commitments of traders")
        SOURCES["cftc"](store)
    if "cboe" in selected:
        logger.info("[backfill] CBOE put/call archives + %d-day daily seed",
                    args.cboe_days)
        backfill_cboe(store, args.cboe_days)
    if "ici" in selected:
        logger.info("[backfill] ICI weekly flows")
        SOURCES["ici"](store)
    if "lseg_skew" in selected:
        backfill_lseg_skew(store, args.skew_years)

    coverage = store.coverage()
    preview = preview_composite(store)

    out_dir = settings.output_root / "regime_backfill" / dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(
        {"coverage": coverage, "composite_preview": {k: v for k, v in preview.items() if k != "series_csv"}},
        indent=2))

    print("\n=== Backfill coverage ===")
    for name, info in sorted(coverage.items()):
        print(f"  {name:24s} {info['rows']:>7} rows  {info['start']} .. {info['end']}")
    if preview.get("latest_score") is not None:
        print(f"\n  Reconstructed composite (latest): {preview['latest_score']}")
    print(f"\nManifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
