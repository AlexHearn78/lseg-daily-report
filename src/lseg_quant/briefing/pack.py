"""Briefing pack: the facts the nightly report narrative is written from.

One JSON document per run with market and macro context, per-ticker price
moves, the top stories, recent earnings calls (with transcript excerpts) and
the HOLDs closest to flipping. The narrative writer may only use what is in
the pack, so every number in the email traces back to LSEG data or the
run's own audit trail.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from lseg_quant.briefing.permids import company_name, transcript_permid
from lseg_quant.mdu.config import UNIVERSE
from lseg_quant.mdu.scoring import BUY_THRESHOLD, SELL_THRESHOLD

logger = logging.getLogger(__name__)

TOP_N = 3
EARNINGS_LOOKBACK_DAYS = 14   # earnings-watch window
FRESH_EARNINGS_DAYS = 5       # calls this recent also get the story boost
EARNINGS_BOOST = 3.0          # story-score bonus for a ticker that just reported
EXCERPT_CHARS = 2400          # transcript text kept per company
TRANSCRIPT_BATCH = 6          # search requests per transcripts call
TRANSCRIPT_TERMS = ["guidance", "outlook", "demand", "margin"]
MACRO_QUERY = ("Federal Reserve, interest rates, inflation and the outlook "
               "for global stock markets")
_HEADLINE_SKIP = ("DIARY", "TABLE-", "Reuters Poll")


@dataclass
class TickerInput:
    """What the pack needs from one ticker's run, already extracted."""

    key: str
    action: str = "HOLD"
    confidence: float | None = None
    composite: float | None = None
    composite_source: str = "none"   # recorded | derived | none
    closes: pd.Series = field(default_factory=lambda: pd.Series(dtype=float))
    headlines: list[dict] = field(default_factory=list)  # {"date", "headline"}
    drivers: list[str] = field(default_factory=list)
    failed: bool = False


def composite_from_hold_confidence(confidence: float) -> float:
    """Invert ``scoring.composite_to_decision``'s HOLD confidence mapping.

    HOLD confidence = 0.5 + composite / (2 * BUY_THRESHOLD), clamped to
    [0.05, 0.98]. Used for runs recorded before composites were audited.
    """
    return round((confidence - 0.5) * 2 * BUY_THRESHOLD, 3)


# ---------------------------------------------------------------------------
# Price moves and story selection
# ---------------------------------------------------------------------------

def _pct(x: float | None) -> float | None:
    return None if x is None else round(float(x) * 100, 2)


def price_moves(closes: pd.Series) -> dict[str, Any]:
    """Day / week / month moves, and today's move vs its normal daily swing."""
    c = closes.dropna().astype(float).sort_index()
    if len(c) < 2:
        return {"as_of": None, "last": None, "ret_1d_pct": None, "ret_5d_pct": None,
                "ret_1m_pct": None, "daily_vol_pct": None, "move_vs_normal": None}
    ret_1d = c.iloc[-1] / c.iloc[-2] - 1
    # Typical daily move over the 30 sessions before today's.
    prior = c.pct_change().dropna().iloc[:-1].tail(30)
    vol = float(prior.std()) if len(prior) >= 10 else None
    return {
        "as_of": str(pd.Timestamp(c.index[-1]).date()),
        "last": round(float(c.iloc[-1]), 2),
        "ret_1d_pct": _pct(ret_1d),
        "ret_5d_pct": _pct(c.iloc[-1] / c.iloc[-6] - 1) if len(c) > 5 else None,
        "ret_1m_pct": _pct(c.iloc[-1] / c.iloc[-22] - 1) if len(c) > 21 else None,
        "daily_vol_pct": _pct(vol),
        "move_vs_normal": round(float(ret_1d / vol), 2) if vol else None,
    }


def story_score(moves: dict[str, Any], reported: bool) -> float:
    """How newsworthy a ticker is tonight: unusual move, plus earnings."""
    return abs(moves.get("move_vs_normal") or 0.0) + (EARNINGS_BOOST if reported else 0.0)


def select_top_stories(entries: list[dict], n: int = TOP_N) -> list[str]:
    ranked = sorted(
        (e for e in entries if not e.get("failed")),
        key=lambda e: (e["story_score"], abs(e["moves"].get("ret_1d_pct") or 0.0)),
        reverse=True,
    )
    return [e["key"] for e in ranked[:n]]


def closest_to_changing(entries: list[dict], n: int = 3) -> list[dict]:
    """HOLDs whose composite score sits nearest the BUY or SELL threshold."""
    out: list[dict] = []
    for e in entries:
        c = e.get("composite")
        if e.get("failed") or e.get("action") != "HOLD" or c is None:
            continue
        to_buy, to_sell = BUY_THRESHOLD - c, c - SELL_THRESHOLD
        nearest, gap = ("BUY", to_buy) if to_buy <= to_sell else ("SELL", to_sell)
        out.append({"key": e["key"], "name": e["name"], "composite": round(c, 3),
                    "nearest_signal": nearest, "gap": round(gap, 3),
                    "drivers": e.get("drivers", [])[:4]})
    return sorted(out, key=lambda x: x["gap"])[:n]


# ---------------------------------------------------------------------------
# LSEG lookups (all optional: the pack still builds without an MCP client)
# ---------------------------------------------------------------------------

def _tool_json(mcp: Any, name: str, args: dict) -> Any:
    resp = mcp.call_tool(name, args)
    result = resp.get("result", {})
    if result.get("isError"):
        raise RuntimeError(f"{name} returned an error")
    content = result.get("content", [])
    text = content[0].get("text", "") if content else ""
    return json.loads(text) if text else {}


def _clean_headlines(items: Any, limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for h in items or []:
        if not isinstance(h, dict):
            continue
        text = " ".join(str(h.get("headlineText") or h.get("headline") or "").split())
        if not text or any(s in text for s in _HEADLINE_SKIP) or text.lower() in seen:
            continue
        seen.add(text.lower())
        date = str(h.get("firstCreated") or h.get("versionCreated") or h.get("date") or "")
        out.append({"date": date[:10], "headline": text[:200]})
        if len(out) >= limit:
            break
    return out


def fetch_closes(mcp: Any, key: str) -> pd.Series:
    df = mcp.get_historical_prices(ric=UNIVERSE[key].ticker, interval="P1D", count=60)
    if df.empty or "close" not in df.columns or "timestamp" not in df.columns:
        return pd.Series(dtype=float)
    return pd.Series(df["close"].astype(float).values, index=pd.to_datetime(df["timestamp"]))


def fetch_company_headlines(mcp: Any, key: str, as_of: dt.date, limit: int = 6) -> list[dict]:
    items = mcp.get_company_news(
        ric=UNIVERSE[key].ric,
        start=(as_of - dt.timedelta(days=3)).isoformat(),
        end=(as_of + dt.timedelta(days=1)).isoformat(),
        headline_only=True,
    )
    return _clean_headlines(items, limit)


def fetch_macro_headlines(mcp: Any, as_of: dt.date, limit: int = 10) -> list[dict]:
    data = _tool_json(mcp, "news_nl_search", {
        "nlQuery": MACRO_QUERY,
        "start": (as_of - dt.timedelta(days=2)).isoformat(),
        "end": (as_of + dt.timedelta(days=1)).isoformat(),
    })
    items = data.get("headlines", []) if isinstance(data, dict) else data
    return _clean_headlines(items, limit)


def _excerpts(results: list[dict], doc_id: Any) -> list[str]:
    """Top-ranked transcript chunks from one document, capped in size."""
    out: list[str] = []
    seen: set[Any] = set()
    total = 0
    for r in results:
        if r.get("perm_id") != doc_id or r.get("chunk_id") in seen:
            continue
        seen.add(r.get("chunk_id"))
        text = " ".join(str(r.get("text", "")).split())[:1200]
        if out and total + len(text) > EXCERPT_CHARS:
            break
        out.append(text)
        total += len(text)
    return out


def find_recent_earnings(mcp: Any, keys: list[str], as_of: dt.date) -> dict[str, dict]:
    """Tickers with an earnings-call transcript in the lookback window.

    Batches one ``financial_document_search`` per company into a few
    transcripts calls; companies that did not report return no results.
    """
    by_permid = {pid: k for k in keys if (pid := transcript_permid(k))}
    since = (as_of - dt.timedelta(days=EARNINGS_LOOKBACK_DAYS)).isoformat()
    permids = list(by_permid)
    out: dict[str, dict] = {}
    for i in range(0, len(permids), TRANSCRIPT_BATCH):
        requests = [{"dataType": "financial_document_search", "options": {
            "search_terms": TRANSCRIPT_TERMS,
            "oa_permids": [pid],
            "data_source_type": ["Earnings Call"],
            "date_specifier": f"{since}/",
        }} for pid in permids[i:i + TRANSCRIPT_BATCH]]
        data = _tool_json(mcp, "transcripts", {"requests": requests})
        for item in data.get("data", []) if isinstance(data, dict) else []:
            pids = (item.get("options") or {}).get("oa_permids") or []
            key = by_permid.get(pids[0]) if pids else None
            results = (item.get("response") or {}).get("results") or []
            if not key or not results:
                continue
            results = sorted(results, key=lambda r: r.get("ranking_score", 0), reverse=True)
            latest = max(results, key=lambda r: str(r.get("publication_date", "")))
            out[key] = {
                "title": latest.get("title"),
                "date": latest.get("publication_date"),
                "excerpts": _excerpts(results, latest.get("perm_id")),
            }
    return out


# ---------------------------------------------------------------------------
# Macro block
# ---------------------------------------------------------------------------

def _value_days_ago(s: pd.Series, days: int) -> float | None:
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return None if past.empty else float(past.iloc[-1])


def macro_block(froth: dict | None, store: Any) -> dict[str, Any]:
    """Froth score, regime and index / rates / credit moves from the store."""
    block: dict[str, Any] = {}
    if froth:
        block["froth"] = {k: froth.get(k) for k in (
            "as_of", "composite_score", "band", "velocity_flag", "structural_score",
            "timing_score", "pillar_scores", "stale_metrics")}
        rg = froth.get("market_regime") or {}
        block["regime"] = {
            "regime": rg.get("regime"),
            "reasons": rg.get("reasons") or [],
            "sp500_trend_3m_pct": _pct(rg.get("trend_3m")),
            "sp500_trend_12m_pct": _pct(rg.get("trend_12m")),
            "funding_stress_percentile": rg.get("funding_stress_pctile"),
        }

    spx = store.read("spx_close").dropna()
    if len(spx) >= 2:
        month_ago = _value_days_ago(spx, 30)
        block["sp500"] = {
            "as_of": str(spx.index[-1].date()),
            "last": round(float(spx.iloc[-1]), 2),
            "ret_1d_pct": _pct(spx.iloc[-1] / spx.iloc[-2] - 1),
            "ret_1m_pct": _pct(spx.iloc[-1] / month_ago - 1) if month_ago else None,
        }
    d10, d2, hy = (store.read(n).dropna() for n in ("dgs10", "dgs2", "hy_oas"))
    if len(d10):
        prev = _value_days_ago(d10, 30)
        block["us_10y_yield"] = {
            "as_of": str(d10.index[-1].date()),
            "last_pct": round(float(d10.iloc[-1]), 2),
            "chg_1m_bp": round((float(d10.iloc[-1]) - prev) * 100) if prev is not None else None,
        }
        if len(d2):
            block["curve_2s10s_bp"] = round((float(d10.iloc[-1]) - float(d2.iloc[-1])) * 100)
    if len(hy):
        prev = _value_days_ago(hy, 30)
        block["high_yield_spread"] = {
            "as_of": str(hy.index[-1].date()),
            "last_pct": round(float(hy.iloc[-1]), 2),
            "chg_1m_bp": round((float(hy.iloc[-1]) - prev) * 100) if prev is not None else None,
        }
    return block


# ---------------------------------------------------------------------------
# Pack assembly
# ---------------------------------------------------------------------------

def build_pack(date: str, tickers: list[TickerInput], froth: dict | None, store: Any,
               mcp: Any = None, as_of: dt.date | None = None) -> dict[str, Any]:
    """Assemble the briefing pack for one run. ``mcp`` enables LSEG lookups."""
    as_of = as_of or dt.date.fromisoformat(date)
    live = [t for t in tickers if not t.failed]

    if mcp is not None:
        for t in live:
            if t.closes.empty:  # runs recorded before price tails were audited
                try:
                    t.closes = fetch_closes(mcp, t.key)
                except Exception as exc:  # noqa: BLE001 - one ticker must not sink the pack
                    logger.warning("briefing: price fetch failed for %s: %s", t.key, exc)

    earnings: dict[str, dict] = {}
    if mcp is not None:
        try:
            earnings = find_recent_earnings(mcp, [t.key for t in live], as_of)
        except Exception as exc:  # noqa: BLE001
            logger.warning("briefing: earnings lookup failed: %s", exc)

    def fresh(key: str) -> bool:
        date = str((earnings.get(key) or {}).get("date") or "")
        try:
            return (as_of - dt.date.fromisoformat(date[:10])).days <= FRESH_EARNINGS_DAYS
        except ValueError:
            return False

    entries: list[dict] = []
    for t in tickers:
        moves = price_moves(t.closes)
        reported = fresh(t.key)
        entries.append({
            "key": t.key,
            "name": company_name(t.key),
            "group": UNIVERSE[t.key].group if t.key in UNIVERSE else "watchlist",
            "failed": t.failed,
            "action": t.action,
            "confidence": t.confidence,
            "composite": t.composite,
            "composite_source": t.composite_source,
            "moves": moves,
            "reported_recently": reported,
            "story_score": round(story_score(moves, reported), 2),
            "drivers": t.drivers[:4],
        })
    by_entry = {e["key"]: e for e in entries}
    by_input = {t.key: t for t in tickers}

    top_stories: list[dict] = []
    for key in select_top_stories(entries):
        e, t = by_entry[key], by_input[key]
        headlines = t.headlines
        if not headlines and mcp is not None:
            try:
                headlines = fetch_company_headlines(mcp, key, as_of)
            except Exception as exc:  # noqa: BLE001
                logger.warning("briefing: headline fetch failed for %s: %s", key, exc)
        why: list[str] = []
        if e["reported_recently"]:
            why.append(f"held an earnings call in the last {FRESH_EARNINGS_DAYS} days")
        if e["moves"].get("move_vs_normal") is not None:
            why.append(f"moved {abs(e['moves']['move_vs_normal']):.1f}x its normal daily swing")
        top_stories.append({
            **{k: e[k] for k in ("key", "name", "group", "action", "confidence", "moves", "drivers")},
            "why_selected": why,
            "headlines": headlines[:6],
            "earnings": earnings.get(key),
        })

    macro_headlines: list[dict] = []
    if mcp is not None:
        try:
            macro_headlines = fetch_macro_headlines(mcp, as_of)
        except Exception as exc:  # noqa: BLE001
            logger.warning("briefing: macro headlines failed: %s", exc)

    actions = [e["action"] for e in entries if not e["failed"]]
    return {
        "date": date,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "summary": {
            "n_tickers": len(entries),
            "n_buy": actions.count("BUY"),
            "n_hold": actions.count("HOLD"),
            "n_sell": actions.count("SELL"),
            "n_failed": sum(e["failed"] for e in entries),
        },
        "thresholds": {"buy": BUY_THRESHOLD, "sell": SELL_THRESHOLD},
        "macro": macro_block(froth, store),
        "macro_headlines": macro_headlines,
        "top_stories": top_stories,
        "earnings_watch": [
            {"key": k, "name": company_name(k), **v}
            for k, v in sorted(earnings.items(), key=lambda kv: str(kv[1].get("date")), reverse=True)
        ],
        "closest_to_changing": closest_to_changing(entries),
        "tickers": entries,
    }
