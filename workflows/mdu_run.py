#!/usr/bin/env python3
"""Analyse one name: LSEG data -> features -> analyst scores -> risk gates -> paper position.

Run:
    uv run workflows/mdu_run.py --ticker MSFT
    uv run workflows/mdu_run.py --ticker MSFT --date 2026-06-01
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dotenv import load_dotenv
load_dotenv()

from lseg_quant.mdu.config import MDUSettings, is_trading_day, lookup_ticker
from lseg_quant.mdu.mcp_client import MCPClient
from lseg_quant.mdu.features import compute_features
from lseg_quant.mdu.currency import align_consensus_currency
from lseg_quant.mdu.decision import LLMDecision, FinalTrade
from lseg_quant.mdu.overrides import RiskOverrideEngine
from lseg_quant.mdu.execution import ExecutionEngine
from lseg_quant.mdu.audit import AuditTrail
from lseg_quant.mdu.research import ResearchContext
from lseg_quant.mdu.analyst import analyze as run_analyst, format_decision as bp_format
from lseg_quant.mdu.research_note import generate_narrative
from lseg_quant.regime.daily import (
    load_latest_context,
    market_context_for_features,
)

logger = logging.getLogger("mdu_run")


def build_risk_context(
    context: ResearchContext,
    features: dict,
    last_price: float,
) -> dict:
    """Build enriched context dict for RiskOverrideEngine."""
    data = context.build()
    core = data["modules"].get("Module 1 \u2014 Equity Core", {})
    val = data["modules"].get("Module 2 \u2014 Valuation", {})

    fwd_pe = None
    current_price = core.get("price", {}).get("current", last_price)
    estimates = val.get("consensus_estimates", [])
    if estimates and current_price > 0:
        fy1_eps = estimates[0].get("eps_est")
        if fy1_eps and fy1_eps > 0:
            fwd_pe = current_price / fy1_eps

    avg_vol = core.get("avg_daily_volume")
    news = data.get("news", {})

    dispersion = val.get("analyst_dispersion")

    risk_context: dict[str, Any] = {
        "data_quality": data.get("data_quality", {}),
        "news_shock": news.get("shock_detected", False),
        "forward_pe": fwd_pe,
        "analyst_dispersion": dispersion,
        "avg_daily_volume": avg_vol,
        "current_price": current_price,
    }
    return risk_context


def apply_risk_overrides(
    llm_decision: LLMDecision,
    features: dict,
    context: ResearchContext,
    last_price: float,
) -> FinalTrade:
    """Apply RiskOverrideEngine (9 gates) to the LLM/analyst decision."""
    risk_context = build_risk_context(context, features, last_price)
    engine = RiskOverrideEngine()
    return engine.apply(llm_decision, features=features, context=risk_context)


def run(as_of: dt.datetime, settings: MDUSettings | None = None,
        ticker_key: str | None = None) -> int:
    if settings is None:
        settings = MDUSettings(calculation_date=as_of)

    date_str = as_of.strftime("%Y-%m-%d")
    run_dir = Path(f"data/outputs/mdu/{as_of.strftime('%Y%m%d')}")
    run_dir.mkdir(parents=True, exist_ok=True)

    audit = AuditTrail(run_dir)
    t0 = time.time()

    # Trading day check
    try:
        info = lookup_ticker(ticker_key or settings.ticker_display)
    except KeyError:
        info = None
    exchange = info.exchange if info else "XNYS"
    if not is_trading_day(as_of, exchange):
        logger.warning("Skipping %s: %s is not a trading day on %s",
                       settings.ticker_display, date_str, exchange)
        return 0

    logger.info("=" * 60)
    logger.info("Daily analyst run \u2014 %s %s", ticker_key or settings.ticker_display, date_str)
    logger.info("=" * 60)

    # ------------------------------------------------------------------
    # 1. Data layer
    # ------------------------------------------------------------------
    logger.info("[1/5] Fetching MCP data...")
    try:
        with MCPClient(
            client_id=settings.lseg_client_id,
            client_secret=settings.lseg_client_secret,
        ) as mcp:
            # A date range, not a count: count requests return only about a
            # month of rows, which leaves the 12-month features empty.
            prices = mcp.get_historical_prices(
                ric=settings.ticker,
                interval="P1D",
                start=(as_of - dt.timedelta(days=365 * settings.lookback_years)).strftime("%Y-%m-%d"),
                end=(as_of + dt.timedelta(days=1)).strftime("%Y-%m-%d"),
            )
            ticker_label = (ticker_key or settings.ticker_display).lower()
            audit.record_data_snapshot(f"{ticker_label}_prices", {
                "rows": len(prices),
                "date_range": (
                    str(prices["timestamp"].min()) if "timestamp" in prices.columns else "N/A",
                    str(prices["timestamp"].max()) if "timestamp" in prices.columns else "N/A",
                ),
            })
            logger.info("  Prices: %d rows", len(prices))
            if {"timestamp", "close"} <= set(prices.columns):
                # Last ~12 months of closes for the nightly briefing's move
                # stats and the report's price charts.
                tail = prices.tail(260)
                audit.record_data_snapshot("price_tail", {
                    "dates": [str(ts)[:10] for ts in tail["timestamp"]],
                    "closes": [round(float(c), 4) for c in tail["close"]],
                })

            if not prices.empty and "close" in prices.columns:
                last_price = float(prices["close"].iloc[-1])
            else:
                last_price = 0.0

            yield_curve = mcp.get_interest_rate_curve(valuation_date=date_str)
            audit.record_data_snapshot("yield_curve", {"rows": len(yield_curve)})
            logger.info("  Yield curve: %d rows", len(yield_curve))

            vol_surface = mcp.get_equity_vol_surface(
                ric=settings.ticker,
                calculation_date=date_str,
            )
            audit.record_data_snapshot("vol_surface", {"rows": len(vol_surface)})
            logger.info("  Vol surface: %d rows", len(vol_surface))

            news_lookback = (as_of - dt.timedelta(days=settings.news_lookback_days)).strftime("%Y-%m-%d")
            news = mcp.get_company_news(
                ric=settings.ric,
                start=news_lookback,
                end=date_str,
            )
            audit.record_data_snapshot("news_headlines", {"count": len(news)})
            audit.record_data_snapshot("news_top", [
                {"date": str(h.get("firstCreated") or h.get("versionCreated") or "")[:10],
                 "headline": (h.get("headlineText") or h.get("headline") or "").strip()}
                for h in news[:8] if isinstance(h, dict)
            ])
            logger.info("  News headlines: %d", len(news))

            ibes = mcp.get_ibes_consensus(
                ticker=settings.ric,
                measures=["Eps", "Rev", "Ebitda", "Dps", "Fcf"],
            )
            ibes, ccy_notes = align_consensus_currency(ibes, settings.currency)
            audit.record_data_snapshot("ibes_consensus", {"count": len(ibes)})
            if ccy_notes:
                audit.record_data_snapshot("consensus_currency_mismatch", ccy_notes)
            logger.info("  IBES estimates: %d periods", len(ibes))

            fundamentals_year = as_of.year - 1
            fundamentals = mcp.get_company_fundamentals(
                identifier=settings.ric,
                measures="1001,2002,5101,4001,18100,18200,3001",
                year=fundamentals_year,
            )
            audit.record_data_snapshot("fundamentals", {"count": len(fundamentals)})
            logger.info("  Fundamentals: %d records", len(fundamentals))

    except Exception as e:
        logger.exception("Data layer failed: %s", e)
        return 1

    # ------------------------------------------------------------------
    # 2. Feature engineering
    # ------------------------------------------------------------------
    logger.info("[2/5] Computing features...")
    market_ctx = market_context_for_features(load_latest_context())
    features = compute_features(
        prices=prices,
        yield_curve=yield_curve,
        news_headlines=news,
        as_of=as_of,
        market_context=market_ctx,
    )
    audit.record_feature_vector(features)
    logger.info("  Features: %s", json.dumps(features, indent=2))

    # ------------------------------------------------------------------
    # 3. LLM decision (research-driven)
    # ------------------------------------------------------------------
    logger.info("[3/5] Analyst scoring...")

    context = ResearchContext(
        ticker=settings.ticker_display,
        prices=prices,
        yield_curve=yield_curve,
        vol_surface=vol_surface,
        news_headlines=news,
        ibes_consensus=ibes,
        fundamentals=fundamentals,
        as_of=as_of,
    )
    bp_decision = run_analyst(context)
    raw = bp_format(bp_decision)
    bp_data = json.loads(raw)
    llm_decision = LLMDecision(
        action=bp_data["action"],
        confidence=bp_data["confidence"],
        allocation=bp_data["allocation"],
        reasoning=bp_data["reasoning"],
        risk_flags=bp_data["risk_flags"],
    )
    audit.record_llm_decision(llm_decision, "automated_analyst", raw)
    audit.record_data_snapshot("analyst_scores", {
        "composite": bp_decision.composite,
        "dimension_scores": bp_decision.dimension_scores,
    })

    logger.info("  LLM decision: %s", json.dumps({
        "action": llm_decision.action,
        "confidence": llm_decision.confidence,
        "allocation": llm_decision.allocation,
    }))

    # ------------------------------------------------------------------
    # 4. Validation + risk engine (9-gate RiskOverrideEngine)
    # ------------------------------------------------------------------
    logger.info("[4/5] Risk validation...")
    final_trade = apply_risk_overrides(llm_decision, features, context, last_price)
    audit.record_final_trade(final_trade)
    logger.info("  Final trade: action=%s alloc=%.1f%% overrides=%s",
                final_trade.action, final_trade.allocation * 100,
                final_trade.risk_overrides or "none")

    # ------------------------------------------------------------------
    # 5. Execution
    # ------------------------------------------------------------------
    logger.info("[5/5] Paper position...")
    engine = ExecutionEngine()
    engine.update_price(last_price)
    result = engine.execute(final_trade, last_price)
    audit.record_execution(result)
    logger.info("  Result: %s", result.message)

    elapsed = time.time() - t0
    audit.write()
    audit.write_summary()

    # Narrative generation for BUY/SELL tickers (skill-file research note)
    if final_trade.action in ("BUY", "SELL"):
        ticker_label = ticker_key or settings.ticker_display
        try:
            note = generate_narrative(
                ticker=ticker_label,
                as_of=date_str,
                context=context,
                bp_decision=bp_decision,
                final_trade=final_trade,
                features=features,
                dimension_scores=bp_decision.dimension_scores,
                composite=bp_decision.composite,
            )
            narrative_path = run_dir / f"narrative_{ticker_label}.md"
            narrative_path.write_text(note.narrative_markdown)
            logger.info("[%s] Research narrative written to %s", ticker_label, narrative_path)
        except Exception as e:
            logger.warning("[%s] Narrative generation skipped: %s", ticker_label, e)

    logger.info("Done \u2014 %.1fs elapsed", elapsed)
    logger.info("Run directory: %s", run_dir)
    return 0


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Analyse one name from config/universe.yaml")
    p.add_argument("--date", type=lambda s: dt.datetime.strptime(s, "%Y-%m-%d"),
                   default=None, help="Calculation date (YYYY-MM-DD)")
    p.add_argument("--ticker", type=str, required=True,
                   help="Key from config/universe.yaml, e.g. MSFT")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    as_of = args.date or dt.datetime.now()
    info = lookup_ticker(args.ticker)
    settings = MDUSettings(
        calculation_date=as_of,
        ticker=info.ticker,
        ric=info.ric,
        ticker_display=info.ticker_display,
        currency=info.currency,
        benchmark_ric=info.benchmark_ric,
    )
    return run(as_of, settings, ticker_key=args.ticker.upper().strip())


if __name__ == "__main__":
    raise SystemExit(main())
