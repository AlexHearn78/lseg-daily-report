from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Module labels matching the skill framework
# ------------------------------------------------------------------

MODULE_CORE = "Module 1 — Equity Core"
MODULE_VALUATION = "Module 2 — Valuation"
MODULE_YIELD = "Module 3 — Government Yield Curves"
MODULE_INFLATION = "Module 4 — Inflation Curves"
MODULE_SWAPS = "Module 5 — Interest Rate Swaps"
MODULE_VOL = "Module 6 — Equity Volatility Surface"
MODULE_OPTIONS = "Module 7 — Options Pricing & Greeks"

# ------------------------------------------------------------------
# Framework constants (from Research_Stock_skill.md)
# ------------------------------------------------------------------

SHOCK_KEYWORDS = [
    "crash", "plunge", "meltdown", "recession", "crisis",
    "bank run", "default", "bailout", "emergency", "flash crash",
]

YIELD_TENOR_LABELS: dict[str, str] = {
    "ON": "O/N", "1W": "1W", "2W": "2W",
    "1M": "1M", "2M": "2M", "3M": "3M",
    "4M": "4M", "5M": "5M", "6M": "6M",
    "7M": "7M", "8M": "8M", "9M": "9M",
    "10M": "10M", "11M": "11M", "1Y": "1Y",
    "18M": "18M", "2Y": "2Y", "3Y": "3Y",
    "4Y": "4Y", "5Y": "5Y", "6Y": "6Y",
    "7Y": "7Y", "8Y": "8Y", "9Y": "9Y",
    "10Y": "10Y", "12Y": "12Y", "15Y": "15Y",
    "20Y": "20Y", "25Y": "25Y", "30Y": "30Y",
}

# ------------------------------------------------------------------
# Research Context
# ------------------------------------------------------------------


class ResearchContext:
    """Structured research context following the LSEG research skill framework.

    Collects MCP-sourced data into the standardised 7-module structure,
    then builds an LLM-ready prompt for BUY / HOLD / SELL decision.
    """

    def __init__(
        self,
        ticker: str,
        prices: pd.DataFrame,
        yield_curve: pd.DataFrame | None = None,
        vol_surface: pd.DataFrame | None = None,
        news_headlines: list[dict] | None = None,
        benchmark_prices: pd.DataFrame | None = None,
        ibes_consensus: list[dict] | None = None,
        fundamentals: list[dict] | None = None,
        as_of: dt.datetime | None = None,
    ) -> None:
        self.ticker = ticker.upper()
        self.as_of = as_of or dt.datetime.now()
        self.prices = prices
        self.yield_curve = yield_curve
        self.vol_surface = vol_surface
        self.news_headlines = news_headlines or []
        self.benchmark_prices = benchmark_prices
        self.ibes_consensus = ibes_consensus or []
        self.fundamentals = fundamentals or []

    def build(self) -> dict[str, Any]:
        """Execute all available modules and return structured context."""
        return {
            "ticker": self.ticker,
            "as_of": self.as_of.isoformat(),
            "modules": {
                MODULE_CORE: self._module_core(),
                MODULE_VALUATION: self._module_valuation(),
                MODULE_YIELD: self._module_yield(),
                MODULE_INFLATION: self._module_inflation(),
                MODULE_SWAPS: self._module_swaps(),
                MODULE_VOL: self._module_vol(),
                MODULE_OPTIONS: self._module_options(),
            },
            "news": self._news_summary(),
            "data_quality": self._data_quality(),
        }

    @staticmethod
    def _ytd_return(close: np.ndarray, timestamps: np.ndarray | None) -> float | None:
        if timestamps is None or len(timestamps) < 2:
            return None
        try:
            start_year = str(timestamps[-1])[:4]
            for i, ts in enumerate(timestamps):
                if str(ts)[:4] != start_year:
                    ytd = (close[-1] / close[i]) - 1
                    return float(ytd)
            return None
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _compute_beta(close: np.ndarray, benchmark_returns: np.ndarray | None = None) -> float | None:
        if benchmark_returns is not None and len(benchmark_returns) >= 21:
            stock_rets = np.diff(np.log(close[close > 0]))
            cov = np.cov(stock_rets[-len(benchmark_returns):], benchmark_returns)
            if cov[0, 1] > 0 and cov[1, 1] > 0:
                return float(cov[0, 1] / cov[1, 1])
        return None

    # ------------------------------------------------------------------
    # Module 1 — Equity Core
    # ------------------------------------------------------------------

    def _module_core(self) -> dict[str, Any]:
        if self.prices.empty or "close" not in self.prices.columns:
            return {"status": "no_data", "reason": "Price data unavailable"}

        close = self.prices["close"].dropna().values
        if len(close) < 2:
            return {"status": "insufficient", "reason": f"Only {len(close)} data points"}

        timestamps = self.prices["timestamp"].dropna().values if "timestamp" in self.prices.columns else None
        log_returns = np.diff(np.log(close[close > 0]))

        current_price = float(close[-1])

        ret_1m = float((close[-1] / close[max(0, len(close) - 22)]) - 1) if len(close) > 22 else float((close[-1] / close[0]) - 1)
        ret_3m = float((close[-1] / close[max(0, len(close) - 63)]) - 1) if len(close) > 63 else None
        ret_12m = float((close[-1] / close[0]) - 1)
        ret_ytd = self._ytd_return(close, timestamps)

        high_52w = float(np.max(close))
        low_52w = float(np.min(close))
        range_pos = (current_price - low_52w) / (high_52w - low_52w) if high_52w > low_52w else 0.5

        vol_21d = float(np.std(log_returns[-21:]) * np.sqrt(252)) if len(log_returns) >= 21 else None
        vol_all = float(np.std(log_returns) * np.sqrt(252))

        peak = np.maximum.accumulate(close)
        dd = (close - peak) / peak
        max_dd = float(np.min(dd))

        avg_volume = None
        vol_trend_pct = None
        if "volume" in self.prices.columns:
            vols = self.prices["volume"].dropna().values
            if len(vols) > 0:
                avg_volume = float(np.mean(vols))
                recent = float(np.mean(vols[-20:])) if len(vols) >= 20 else avg_volume
                older = float(np.mean(vols[:60])) if len(vols) >= 60 else avg_volume
                if older > 0:
                    vol_trend_pct = round(((recent / older) - 1) * 100, 1)

        beta = self._compute_beta(close)

        return {
            "status": "available",
            "price": {
                "current": round(current_price, 2),
                "high_52w": round(high_52w, 2),
                "low_52w": round(low_52w, 2),
                "range_position_pct": round(range_pos * 100, 1),
            },
            "returns": {
                "1m": round(ret_1m * 100, 2),
                "3m": round(ret_3m * 100, 2) if ret_3m is not None else None,
                "ytd": round(ret_ytd * 100, 2) if ret_ytd is not None else None,
                "total_return": round(ret_12m * 100, 2),
            },
            "volatility": {
                "21d_annualised": round(vol_21d * 100, 2) if vol_21d is not None else None,
                "full_sample_annualised": round(vol_all * 100, 2),
            },
            "max_drawdown": round(max_dd * 100, 2),
            "beta_vs_benchmark": round(beta, 2) if beta is not None else None,
            "avg_daily_volume": round(avg_volume, 0) if avg_volume else None,
            "volume_trend_pct": vol_trend_pct,
            "data_points": len(close),
        }

    # ------------------------------------------------------------------
    # Module 2 — Valuation
    # ------------------------------------------------------------------

    _IBES_MEASURE_MAP = {
        "epsPerShare": "eps_est",
        "revMillion": "rev_est_m",
        "ebitdaPerShare": "ebitda_ps",
        "ebitdaMillion": "ebitda_est_m",
        "dividendPerShare": "dps_est",
        "fcfPerShare": "fcf_ps",
    }

    def _module_valuation(self) -> dict[str, Any]:
        result: dict[str, Any] = {"status": "no_data"}

        if self.ibes_consensus:
            estimates = []
            for est in self.ibes_consensus:
                measures = est.get("measures", {})
                row: dict[str, Any] = {"fiscal_year": est.get("date", "")[:4]}
                for raw_key, out_key in self._IBES_MEASURE_MAP.items():
                    raw = measures.get(raw_key, {})
                    val = raw.get("value")
                    if val is not None:
                        try:
                            row[out_key] = round(float(val), 2)
                        except (ValueError, TypeError):
                            row[out_key] = val
                estimates.append(row)

            # Derive analyst dispersion from FY0 EPS high/low/mean
            dispersion = None
            first_raw = self.ibes_consensus[0] if self.ibes_consensus else {}
            eps_measure = first_raw.get("measures", {}).get("epsPerShare", {})
            if eps_measure:
                high = eps_measure.get("high")
                low = eps_measure.get("low")
                mean = eps_measure.get("value")
                if high is not None and low is not None and mean and float(mean) != 0:
                    dispersion = round((float(high) - float(low)) / abs(float(mean)), 4)
                elif eps_measure.get("standardDeviation") and mean and float(mean) != 0:
                    dispersion = round(
                        float(eps_measure["standardDeviation"]) / abs(float(mean)), 4
                    )
            if dispersion is not None:
                result["analyst_dispersion"] = dispersion

            result["consensus_estimates"] = estimates
            result["status"] = "available"
            result["source"] = "qa_ibes_consensus"

        if self.fundamentals:
            items = {}
            for f in self.fundamentals:
                name = f.get("itemName", f"item_{f.get('item')}")
                val = f.get("value")
                units = f.get("itemUnits", "")
                year = f.get("year")
                items[name] = {"value": val, "units": units, "year": year}

            result["fundamentals"] = items
            result["fundamentals_raw"] = {
                "revenue": self._fund_val(items, "Total Revenue"),
                "net_income": self._fund_val(items, "Net Income"),
                "equity": self._fund_val(items, "Total Equity"),
                "shares_outstanding": self._fund_val(items, "Common Shares Outstanding"),
                "total_debt": self._fund_val(items, "Total Debt"),
                "cash": self._fund_val(items, "Cash & Short Term Investment"),
                "operating_cf": self._fund_val(items, "Cash from Operations"),
            }
            if result.get("status") == "no_data":
                result["status"] = "available"
                result["source"] = "qa_company_fundamentals"

        if result.get("status") == "no_data":
            result["note"] = "No consensus estimates or fundamentals available."

        return result

    @staticmethod
    def _fund_val(items: dict, name: str) -> float | None:
        entry = items.get(name)
        if entry:
            try:
                return float(entry["value"])
            except (ValueError, TypeError):
                return None
        return None

    # ------------------------------------------------------------------
    # Module 3 — Yield Curves
    # ------------------------------------------------------------------

    def _module_yield(self) -> dict[str, Any]:
        if self.yield_curve is None or self.yield_curve.empty:
            return {"status": "no_data", "reason": "Yield curve data not available"}

        tenors_rates: list[dict[str, float]] = []

        if "tenor" in self.yield_curve.columns and "rate" in self.yield_curve.columns:
            for _, row in self.yield_curve.iterrows():
                tenor = str(row.get("tenor", ""))
                rate_val = row.get("rate")
                if rate_val is not None:
                    label = YIELD_TENOR_LABELS.get(tenor, tenor)
                    tenors_rates.append({"tenor": label, "rate_pct": round(float(rate_val), 2)})
        else:
            for _, row in self.yield_curve.iterrows():
                definition = row.get("definition", {})
                tenor = definition.get("tenor", "") if isinstance(definition, dict) else ""
                label = YIELD_TENOR_LABELS.get(tenor, tenor)
                quote = row.get("quote", {})
                values = quote.get("values", {}) if isinstance(quote, dict) else {}
                bid = values.get("bid") or values.get("value")
                ask = values.get("ask") or values.get("value")
                if isinstance(bid, dict):
                    bid = bid.get("value")
                if isinstance(ask, dict):
                    ask = ask.get("value")
                if bid is not None and ask is not None:
                    mid = (float(bid) + float(ask)) / 2
                    tenors_rates.append({"tenor": label, "rate_pct": round(mid, 2)})

        metrics: dict[str, Any] = {"tenors": tenors_rates}
        rates_by_label = {t["tenor"]: t["rate_pct"] for t in tenors_rates}

        if "2Y" in rates_by_label and "10Y" in rates_by_label:
            spread = rates_by_label["10Y"] - rates_by_label["2Y"]
            metrics["2s10s_spread"] = round(spread, 2)
            metrics["curve_shape"] = "inverted" if spread < 0 else ("steep" if spread > 0.5 else "flat")

        if "10Y" in rates_by_label:
            metrics["10y_nominal"] = rates_by_label["10Y"]

        return {"status": "available", "metrics": metrics}

    # ------------------------------------------------------------------
    # Module 4 — Inflation
    # ------------------------------------------------------------------

    def _module_inflation(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "note": "Inflation breakevens require qa_macroeconomic entitlement (403). "
                    "CPI / PPI data not accessible with current API key.",
        }

    # ------------------------------------------------------------------
    # Module 5 — Interest Rate Swaps
    # ------------------------------------------------------------------

    def _module_swaps(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "note": "IRS data not available with current entitlement. "
                    "Use yield curve 5Y/10Y as rough WACC proxy.",
        }

    # ------------------------------------------------------------------
    # Module 6 — Equity Vol Surface
    # ------------------------------------------------------------------

    def _module_vol(self) -> dict[str, Any]:
        if self.vol_surface is None or self.vol_surface.empty:
            return {
                "status": "no_data",
                "note": "Vol surface not fetched in this run cycle.",
            }

        try:
            str_cols = [float(c) for c in self.vol_surface.columns]
            expiries = list(self.vol_surface.index)

            atm_idx = min(str_cols, key=lambda x: abs(x - 100))
            atm_col = self.vol_surface[atm_idx]

            atm_iv_1m = float(atm_col.iloc[0])

            nearest_80 = min(str_cols, key=lambda x: abs(x - 80))
            nearest_90 = min(str_cols, key=lambda x: abs(x - 90))
            nearest_110 = min(str_cols, key=lambda x: abs(x - 110))
            nearest_120 = min(str_cols, key=lambda x: abs(x - 120))

            grid: dict[str, dict[str, float]] = {}
            expiry_labels = ["1w", "1m", "3m", "6m", "1y"]
            for i, expiry in enumerate(expiries[:5]):
                label = expiry_labels[i] if i < len(expiry_labels) else str(expiry)
                grid[label] = {
                    "atm": round(float(self.vol_surface[atm_idx].iloc[i]) * 100, 2),
                }
                if nearest_80 in str_cols:
                    grid[label]["80p"] = round(float(self.vol_surface[nearest_80].iloc[i]) * 100, 2)
                if nearest_90 in str_cols:
                    grid[label]["90p"] = round(float(self.vol_surface[nearest_90].iloc[i]) * 100, 2)
                if nearest_110 in str_cols:
                    grid[label]["110p"] = round(float(self.vol_surface[nearest_110].iloc[i]) * 100, 2)
                if nearest_120 in str_cols:
                    grid[label]["120p"] = round(float(self.vol_surface[nearest_120].iloc[i]) * 100, 2)

            skew_1m = None
            butterfly_1m = None
            if "1m" in grid and "90p" in grid["1m"] and "110p" in grid["1m"]:
                skew_1m = round(grid["1m"]["90p"] - grid["1m"]["110p"], 2)
                butterfly_1m = round(
                    (grid["1m"]["90p"] + grid["1m"]["110p"]) / 2 - grid["1m"]["atm"], 2
                )

            result: dict[str, Any] = {
                "status": "available",
                "atm_iv_1m_pct": grid.get("1m", {}).get("atm"),
                "atm_4w_pct": grid.get("1w", {}).get("atm"),
                "atm_3m_pct": grid.get("3m", {}).get("atm"),
                "atm_1y_pct": grid.get("1y", {}).get("atm"),
                "grid": grid,
            }
            if skew_1m is not None:
                result["skew_1m_90_110"] = skew_1m
            if butterfly_1m is not None:
                result["butterfly_1m"] = butterfly_1m

            if atm_iv_1m > 0:
                iv_series = [float(self.vol_surface[atm_idx].iloc[i]) for i in range(len(expiries))]
                iv_mean = float(np.mean(iv_series))
                if iv_mean > 0:
                    iv_percentile = sum(1 for v in iv_series if v <= atm_iv_1m) / len(iv_series) * 100
                    result["atm_iv_vs_avg"] = round((atm_iv_1m / iv_mean - 1) * 100, 1)
                    result["atm_iv_percentile"] = round(iv_percentile, 0)

            return result
        except Exception as e:
            return {"status": "error", "reason": f"Could not parse vol surface: {e}"}

    @staticmethod
    def _nearest_strike_row(df: pd.DataFrame) -> pd.Series | None:
        if df.empty:
            return None
        str_cols = [float(c) for c in df.columns]
        nearest_strike = min(str_cols, key=lambda x: abs(x - 100))
        return df[nearest_strike]

    # ------------------------------------------------------------------
    # Module 7 — Options Pricing
    # ------------------------------------------------------------------

    def _module_options(self) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "note": "Options chain data not available with current entitlement.",
        }

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def _news_summary(self) -> dict[str, Any]:
        headlines = []
        has_shock = False
        for h in self.news_headlines:
            text = (h.get("headlineText") or h.get("headline") or "").strip()
            if text:
                headlines.append(text)
            if not has_shock:
                lower = text.lower()
                if any(kw in lower for kw in SHOCK_KEYWORDS):
                    has_shock = True

        return {
            "total_headlines": len(headlines),
            "headlines": headlines[:5],
            "shock_detected": has_shock,
        }

    # ------------------------------------------------------------------
    # Data quality
    # ------------------------------------------------------------------

    def _data_quality(self) -> dict[str, str]:
        return {
            "price_data": "ok" if not self.prices.empty else "missing",
            "yield_curve": "ok" if self.yield_curve is not None and not self.yield_curve.empty else "missing",
            "vol_surface": "ok" if self.vol_surface is not None and not self.vol_surface.empty else "missing",
            "news": "ok" if self.news_headlines else "missing",
            "consensus_estimates": "ok" if self.ibes_consensus else "missing",
            "fundamentals": "ok" if self.fundamentals else "missing",
        }


# ------------------------------------------------------------------
# Prompt builder
# ------------------------------------------------------------------

def build_research_prompt(context: ResearchContext) -> str:
    """Build the full user prompt for the LLM, following the skill framework."""
    data = context.build()
    ticker = data["ticker"]
    as_of = data["as_of"]

    lines: list[str] = [
        f"# Equity Research — {ticker} (as of {as_of[:10]})",
        "",
        "---",
        "",
        "## Investment Thesis — Required Output",
        "",
        "Based on the structured data below, produce a BUY / HOLD / SELL recommendation "
        "with the following JSON output:",
        "",
        "```json",
        '{ "action": "BUY" | "HOLD" | "SELL",',
        '  "confidence": <0.0–1.0>,',
        '  "allocation": <0.0–1.0 as fraction of portfolio>,',
        '  "reasoning": ["bullet 1", "bullet 2", ...],',
        '  "risk_flags": ["flag1", "flag2", ...] }',
        "```",
        "",
        "Follow the 5-section research framework in your reasoning:",
        "1. Investment Thesis — one-line characterisation + 4-7 bullets with numbers",
        "2. Narrative Tone — declare which style you use (Conviction-Led / Evidence-First / Contrarian / Thematic)",
        "3. Business Context — what does consensus assume?",
        "4. Analytical Core — 3-6 thesis pillars, driven by the data below",
        "5. Risks — quantified, with mitigants",
        "",
        "---",
        "",
    ]

    # Module 1 — Equity Core
    core = data["modules"][MODULE_CORE]
    lines.append(f"## {MODULE_CORE}")
    if core.get("status") == "available":
        p = core["price"]
        r = core["returns"]
        v = core["volatility"]
        lines.append(f"- Price: ${p['current']} | 52w range: ${p['low_52w']}–${p['high_52w']} "
                      f"| Position: {p['range_position_pct']}% of range")
        lines.append(f"- Returns: 1m={r['1m']}% | 3m={r['3m']}%" if r.get("3m") is not None
                      else f"- Returns: 1m={r['1m']}% | total={r['total_return']}%")
        lines.append(f"- Vol: 21d ann={v['21d_annualised']}%" if v.get("21d_annualised") else "")
        lines.append(f"- Max drawdown: {core['max_drawdown']}%")
        if core.get("avg_daily_volume"):
            lines.append(f"- Avg daily volume: {core['avg_daily_volume']:,.0f}")
        lines.append(f"- Data points: {core['data_points']} trading days")
    else:
        lines.append(f"- Status: {core.get('status')} — {core.get('reason', '')}")
    lines.append("")

    # Module 2 — Valuation
    val_mod = data["modules"][MODULE_VALUATION]
    lines.append(f"## {MODULE_VALUATION}")
    if val_mod.get("status") == "available":
        if val_mod.get("consensus_estimates"):
            lines.append("### Consensus Estimates (IBES)")
            for e in val_mod["consensus_estimates"]:
                eps_str = f"EPS: ${e['eps_est']}" if e.get("eps_est") else "EPS: N/A"
                rev_str = f"Rev: ${e['rev_est_m']}M" if e.get("rev_est_m") else "Rev: N/A"
                lines.append(f"- FY{e['fiscal_year']}: {eps_str} | {rev_str}")
        if val_mod.get("fundamentals"):
            lines.append("### Reported Fundamentals")
            for name, v in val_mod["fundamentals"].items():
                lines.append(f"- {name}: {v['value']} {v['units']} (FY{v['year']})")
    else:
        lines.append(f"- Status: {val_mod.get('status')} — {val_mod.get('note', '')}")
    lines.append("")

    # Module 3 — Yield Curve
    yield_mod = data["modules"][MODULE_YIELD]
    lines.append(f"## {MODULE_YIELD}")
    if yield_mod.get("status") == "available":
        m = yield_mod["metrics"]
        for t in m.get("tenors", []):
            lines.append(f"- {t['tenor']}: {t['rate_pct']}%")
        if "2s10s_spread" in m:
            lines.append(f"- 2s10s spread: {m['2s10s_spread']}bps ({m['curve_shape']})")
    else:
        lines.append(f"- Status: {yield_mod.get('status')} — {yield_mod.get('reason', '')}")
    lines.append("")

    # Module 6 — Vol Surface
    vol_mod = data["modules"][MODULE_VOL]
    lines.append(f"## {MODULE_VOL}")
    lines.append(f"- Status: {vol_mod.get('status')}")
    if vol_mod.get("status") == "available":
        lines.append(f"- ATM IV (1m): {vol_mod['atm_iv_1m_pct']}%")
    elif vol_mod.get("note"):
        lines.append(f"- Note: {vol_mod['note']}")
    lines.append("")

    # News
    news = data["news"]
    lines.append("## News Sentiment")
    lines.append(f"- Headlines in window: {news['total_headlines']}")
    if news["headlines"]:
        for h in news["headlines"]:
            lines.append(f"  - {h}")
    lines.append(f"- Shock keywords detected: {news['shock_detected']}")
    lines.append("")

    # Data quality
    dq = data["data_quality"]
    lines.append("## Data Quality Notes")
    missing = [k for k, v in dq.items() if v.startswith("missing")]
    if missing:
        lines.append(f"- **Unavailable:** {', '.join(missing)}")
        lines.append("  - These modules use conservative defaults in features.")
    available = [k for k, v in dq.items() if v.startswith("ok")]
    if available:
        lines.append(f"- **Available:** {', '.join(available)}")
    lines.append("")

    # Unavailable modules
    for mod_name in [MODULE_INFLATION, MODULE_SWAPS, MODULE_OPTIONS]:
        mod = data["modules"][mod_name]
        if mod.get("status") == "unavailable":
            lines.append(f"## {mod_name}")
            lines.append(f"- {mod.get('note', 'Data unavailable.')}")
            lines.append("")

    # Final instruction
    lines.append("---")
    lines.append("")
    lines.append("**Output only valid JSON matching the schema above. "
                  "Confidence must be < 1.0. Allocation is a fraction of portfolio (0.0–1.0). "
                  "Include 3-5 reasoning bullets with specific numbers from this data.**")

    return "\n".join(lines)
