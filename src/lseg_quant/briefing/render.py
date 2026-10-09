"""Briefing sections: validation, template fallback and house-style HTML.

``sections.json`` (written by the Claude step) is validated against the pack;
when it is missing or malformed the deterministic template below is used, so
the email always carries a market section. The HTML blocks use
``lseg_quant.reporting.theme`` so the email body and the attached report
share one look.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any

from lseg_quant.reporting.clickthrough import NARROW, SERIES_1, click_attrs, svg_line_chart
from lseg_quant.reporting.theme import (
    ACTION_COLORS,
    AMBER,
    BLUE,
    BORDER,
    FAINT,
    GREEN,
    HEADING,
    INSET,
    MONO,
    MUTED,
    RED,
    ROW_RULE,
    SANS,
    SUBTLE,
    TEXT,
    bar_rows,
    chip,
    email_row,
    esc,
    fmt_pct,
    froth_color,
    inset,
    mono,
    note,
    prose,
    section,
    sign_color,
    stat_tiles,
)

logger = logging.getLogger(__name__)

SECTIONS_FILE = "sections.json"
PACK_FILE = "pack.json"
CHARTS_FILE = "charts.json"
LOOKUPS_FILE = "lookups.json"  # live news results the writer logged


def _text(value: Any, limit: int) -> str:
    """Whitespace-normalised text that keeps blank-line paragraph breaks."""
    if not isinstance(value, str):
        return ""
    paras = [" ".join(p.split()) for p in value.split("\n\n")]
    return "\n\n".join(p for p in paras if p)[:limit]


def _sources(raw: Any, read_headlines: set[str]) -> list[dict]:
    """Cited stories, kept only when the writer read them: the headline is in
    the pack or in the writer's logged ``lookups.json``."""
    out: list[dict] = []
    for src in raw if isinstance(raw, list) else []:
        if not isinstance(src, dict):
            continue
        headline = _text(src.get("headline"), 200)
        if not headline:
            continue
        if headline.lower() not in read_headlines:
            logger.warning("briefing: dropped a cited source that was not read: %r", headline)
            continue
        out.append({"source": _text(src.get("source"), 40), "date": _text(src.get("date"), 10),
                    "headline": headline})
    return out[:3]


def validate_sections(raw: Any, pack: dict, lookups: Any = None) -> dict | None:
    """Cleaned sections, or None when the shape is unusable.

    *lookups* is the writer's ``lookups.json`` (live news it relied on).
    """
    if not isinstance(raw, dict):
        return None
    macro = _text(raw.get("market_macro"), 1600)
    if not macro:
        return None
    known = {e["key"] for e in pack.get("tickers", [])}
    read: dict[str, set[str]] = {
        s["key"]: {h["headline"].lower() for h in (s.get("stories") or []) + (s.get("headlines") or [])
                   if h.get("headline")}
        for s in pack.get("top_stories", [])}
    for lk in lookups if isinstance(lookups, list) else []:
        if isinstance(lk, dict) and lk.get("ticker") and lk.get("headline"):
            read.setdefault(str(lk["ticker"]).upper(), set()).add(str(lk["headline"]).lower())

    def items(name: str, limit: int, cap: int, cited: bool = False) -> list[dict]:
        out: list[dict] = []
        for it in raw.get(name) or []:
            if not isinstance(it, dict):
                continue
            ticker = str(it.get("ticker", "")).upper()
            body = _text(it.get("body"), limit)
            if ticker in known and body:
                item = {"ticker": ticker, "title": _text(it.get("title"), 120), "body": body}
                if cited:
                    item["sources"] = _sources(it.get("sources"), read.get(ticker, set()))
                out.append(item)
        return out[:cap]

    return {
        "source": "claude",
        "headline": _text(raw.get("headline"), 240),
        "market_macro": macro,
        "top_stories": items("top_stories", 1400, 3, cited=True),
        "earnings_watch": items("earnings_watch", 500, 8),
        "closest_to_changing": items("closest_to_changing", 300, 3),
    }


def template_sections(pack: dict) -> dict:
    """Plain, factual sections built straight from the pack (no LLM)."""
    m = pack.get("macro") or {}
    parts: list[str] = []
    spx = m.get("sp500") or {}
    if spx:
        parts.append(f"The S&P 500 moved {fmt_pct(spx.get('ret_1d_pct'))} on the day "
                     f"and {fmt_pct(spx.get('ret_1m_pct'))} over the past month.")
    y10, hy = m.get("us_10y_yield") or {}, m.get("high_yield_spread") or {}
    if y10:
        parts.append(f"The 10-year Treasury yield is {y10['last_pct']:.2f}%"
                     + (f" ({y10['chg_1m_bp']:+d} bp in a month)." if y10.get("chg_1m_bp") is not None else "."))
    if hy:
        parts.append(f"High-yield credit spreads are {hy['last_pct']:.2f}%"
                     + (f" ({hy['chg_1m_bp']:+d} bp in a month)." if hy.get("chg_1m_bp") is not None else "."))
    risk: list[str] = []
    fr = m.get("froth") or {}
    if fr.get("composite_score") is not None:
        risk.append(f"The froth score is {fr['composite_score']:.0f}/100 ({fr.get('band', 'n/a')}), "
                    f"velocity {str(fr.get('velocity_flag', 'n/a')).replace('_', ' ')}.")
        pillars = {k: v for k, v in (fr.get("pillar_scores") or {}).items() if v is not None}
        if pillars:
            hi, lo = max(pillars, key=pillars.get), min(pillars, key=pillars.get)
            risk.append(f"The most stretched pillar is {hi} ({pillars[hi]:.0f}); "
                        f"the calmest is {lo} ({pillars[lo]:.0f}).")
    rg = m.get("regime") or {}
    if rg.get("regime"):
        reason = f": {rg['reasons'][0]}" if rg.get("reasons") else ""
        risk.append(f"The market regime reads {rg['regime'].replace('_', ' ')}{reason}.")
    paras = [" ".join(parts), " ".join(risk)]
    macro = "\n\n".join(p for p in paras if p) or "Market data was unavailable for this run."

    stories: list[dict] = []
    for s in pack.get("top_stories", []):
        mv = s.get("moves") or {}
        body = f"{s['name']} moved {fmt_pct(mv.get('ret_1d_pct'))} on the day"
        if mv.get("move_vs_normal") is not None:
            body += f", about {abs(mv['move_vs_normal']):.1f}x a normal day"
        body += f", and {fmt_pct(mv.get('ret_1m_pct'))} over a month."
        if s.get("earnings"):
            body += f" Latest earnings call: {s['earnings']['title']} ({s['earnings']['date']})."
        if s.get("headlines"):
            body += f" Latest headline: {s['headlines'][0]['headline']}."
        body += f" Model view: {s['action']}."
        stories.append({"ticker": s["key"], "title": f"{s['name']} {fmt_pct(mv.get('ret_1d_pct'))}",
                        "body": body})

    earnings = [{"ticker": e["key"], "title": e["name"], "body": f"{e['title']} ({e['date']})."}
                for e in pack.get("earnings_watch", [])]
    closest = [{"ticker": c["key"], "title": c["name"],
                "body": f"Composite score {c['composite']:+.2f}, {c['gap']:.2f} away from a "
                        f"{c['nearest_signal']} signal."}
               for c in pack.get("closest_to_changing", [])]

    summary = pack.get("summary") or {}
    headline = (f"{summary.get('n_buy', 0)} BUY, {summary.get('n_hold', 0)} HOLD, "
                f"{summary.get('n_sell', 0)} SELL")
    if stories:
        headline += f"; biggest story: {stories[0]['title']}"
    return {"source": "template", "headline": headline, "market_macro": macro,
            "top_stories": stories, "earnings_watch": earnings, "closest_to_changing": closest}


def load_charts(briefing_dir: Path) -> dict:
    """The run's 12-month chart series (``charts.json``); empty when absent."""
    path = briefing_dir / CHARTS_FILE
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("briefing: unreadable %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def load_briefing(briefing_dir: Path) -> tuple[dict | None, dict | None]:
    """(sections, pack) for a run; template sections when Claude's are unusable."""
    pack_path = briefing_dir / PACK_FILE
    if not pack_path.is_file():
        return None, None
    try:
        pack = json.loads(pack_path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("briefing: unreadable %s: %s", pack_path, exc)
        return None, None
    sec_path = briefing_dir / SECTIONS_FILE
    lookups = None
    if (briefing_dir / LOOKUPS_FILE).is_file():
        try:
            lookups = json.loads((briefing_dir / LOOKUPS_FILE).read_text())
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("briefing: unreadable %s: %s", briefing_dir / LOOKUPS_FILE, exc)
    if sec_path.is_file():
        try:
            cleaned = validate_sections(json.loads(sec_path.read_text()), pack, lookups)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("briefing: unreadable %s: %s", sec_path, exc)
            cleaned = None
        if cleaned:
            return cleaned, pack
        logger.warning("briefing: %s failed validation, using template", sec_path)
    return template_sections(pack), pack


# ---------------------------------------------------------------------------
# HTML blocks (house style; shared by the email body and the attached report)
# ---------------------------------------------------------------------------

def _headline(text: str) -> str:
    return (f'<div style="font-family:{SANS};font-size:17px;font-weight:500;color:{HEADING};'
            f'line-height:1.45;border-left:3px solid {BLUE};padding:2px 0 2px 12px">{esc(text)}</div>')


def _bp(value: float | None) -> str | None:
    return None if value is None else f"1M {value:+.0f}bp"


def _pillar_bars(pillars: list[tuple[str, float]], details: dict[str, str] | None) -> str:
    """The froth pillar bars; with *details*, each opens its drilldown below it."""
    rows = [(k, (k.capitalize(), v / 100, froth_color(v), f"{v:.0f}", "")) for k, v in pillars]
    if not details:
        return bar_rows([r for _, r in rows], label_width=84)
    out = []
    for key, row in rows:
        bar_html = bar_rows([row], label_width=84)
        if not details.get(key):
            out.append(bar_html)
            continue
        panel = f"risk-{key}"
        out.append(f'<div class="detail-click" '
                   f'{click_attrs(panel, f"Show what is behind the {key} score")}>{bar_html}</div>'
                   f'<div class="detail-panel" id="{panel}">{details[key]}</div>')
    return f'<div class="detail-bars">{"".join(out)}</div>'


def _value_days_before(points: list[list[Any]], days: int) -> float | None:
    """The last value at least *days* calendar days before the final point."""
    cutoff = dt.date.fromisoformat(points[-1][0]) - dt.timedelta(days=days)
    before = [v for d, v in points if dt.date.fromisoformat(d) <= cutoff]
    return before[-1] if before else None


def series_panel(title: str, points: list[list[Any]], kind: str = "price",
                 moves: dict | None = None) -> str:
    """A 12-month chart with its headline facts, for a detail panel.

    *kind* sets the units: ``price`` (changes in percent), ``pct`` (a yield or
    spread in percent; changes in basis points) or ``bp`` (a curve in basis
    points, with a zero line).
    """
    if len(points) < 2:
        return note("No history was saved for this run.")
    vals = [p[1] for p in points]
    last, first, hi, lo = vals[-1], vals[0], max(vals), min(vals)
    month = _value_days_before(points, 30)
    if kind == "price":
        fmt = lambda v: f"{v:,.2f}"  # noqa: E731
        facts = [f"last {fmt(last)}"]
        if moves and moves.get("ret_1d_pct") is not None:
            facts.append(f"1D {fmt_pct(moves['ret_1d_pct'])}")
        if moves and moves.get("ret_1m_pct") is not None:
            facts.append(f"1M {fmt_pct(moves['ret_1m_pct'])}")
        facts.append(f"12M {fmt_pct((last / first - 1) * 100)}")
    else:
        scale = 100 if kind == "pct" else 1   # changes always in basis points
        fmt = (lambda v: f"{v:.2f}%") if kind == "pct" else (lambda v: f"{v:+.0f}bp")  # noqa: E731
        facts = [f"last {fmt(last)}"]
        if month is not None:
            facts.append(f"1M {(last - month) * scale:+.0f}bp")
        facts.append(f"12M {(last - first) * scale:+.0f}bp")
    if hi > lo:
        facts.append(f"12M range {fmt(lo)} to {fmt(hi)} "
                     f"({(last - lo) / (hi - lo) * 100:.0f}% of the way up)")
    y_range = None
    if kind == "bp":  # always show zero, so the distance to inversion is visible
        lo0, hi0 = min(lo, 0.0), max(hi, 0.0)
        pad = (hi0 - lo0 or 1.0) * 0.08
        y_range = (lo0 - pad, hi0 + pad)
    chart = svg_line_chart([(title, SERIES_1, points)], value_fmt=fmt, y_range=y_range,
                           ref_lines=[(0.0, "inverted below")] if kind == "bp" else None,
                           title=f"{title}, daily, 12 months", width=NARROW, surface=INSET)
    return (f'<div style="font-family:{MONO};font-size:11px;color:{SUBTLE};margin-bottom:4px">'
            f'{esc(" · ".join(facts))}</div>{chart}')


# Dashboard tiles that open a 12-month chart: key -> (chart title, units).
_TILE_CHARTS: dict[str, tuple[str, str]] = {
    "sp500": ("S&P 500", "price"),
    "ftse_all_world": ("FTSE All-World (USD)", "price"),
    "us_10y_yield": ("US 10-year Treasury yield", "pct"),
    "curve_2s10s_bp": ("2s10s curve (10-year minus 2-year)", "bp"),
    "high_yield_spread": ("US high-yield spread (ICE BofA OAS)", "pct"),
}


def _dashboard(pack: dict, details: dict[str, str] | None = None,
               charts: dict | None = None) -> str:
    """Index, rates and credit tiles, then the four froth pillars as bars.

    *details* maps a pillar to its drilldown HTML and *charts* holds the
    12-month index series (attached report only; the email passes neither,
    so its tiles and bars are not clickable).
    """
    m = pack.get("macro") or {}
    tiles: list[tuple[str, str, str, str | None]] = []
    index_keys: list[str | None] = []
    spx = m.get("sp500") or {}
    if spx.get("ret_1d_pct") is not None:
        tiles.append(("S&P 500 1D", fmt_pct(spx["ret_1d_pct"]), sign_color(spx["ret_1d_pct"]),
                      f"1M {fmt_pct(spx.get('ret_1m_pct'))}"))
        index_keys.append("sp500")
    aw = m.get("ftse_all_world") or {}
    if aw.get("ret_1d_pct") is not None:
        tiles.append(("FTSE All-World 1D", fmt_pct(aw["ret_1d_pct"]), sign_color(aw["ret_1d_pct"]),
                      f"1M {fmt_pct(aw.get('ret_1m_pct'))}"))
        index_keys.append("ftse_all_world")
    y10 = m.get("us_10y_yield") or {}
    if y10.get("last_pct") is not None:
        tiles.append(("US 10Y", f"{y10['last_pct']:.2f}%", HEADING, _bp(y10.get("chg_1m_bp"))))
        index_keys.append("us_10y_yield")
    curve = m.get("curve_2s10s_bp")
    if curve is not None:
        tiles.append(("2s10s", f"{curve:+.0f}bp", HEADING if curve >= 0 else RED, None))
        index_keys.append("curve_2s10s_bp")
    hy = m.get("high_yield_spread") or {}
    if hy.get("last_pct") is not None:
        tiles.append(("HY spread", f"{hy['last_pct']:.2f}%", HEADING, _bp(hy.get("chg_1m_bp"))))
        index_keys.append("high_yield_spread")
    parts: list[str] = []
    if tiles:
        series = (charts or {}).get("indices") or {}
        attrs, panels = [], []
        for key in index_keys:
            if key and series.get(key):
                pid = f"px-{key}"
                name, kind = _TILE_CHARTS[key]
                attrs.append(f'class="detail-click" '
                             f'{click_attrs(pid, f"Show the {name} over 12 months")}')
                panels.append(f'<div class="detail-panel" id="{pid}">'
                              f'{series_panel(name, series[key], kind, m.get(key))}</div>')
            else:
                attrs.append("")
        parts.append(stat_tiles(tiles, size=16, base=INSET,
                                cell_attrs=attrs if panels else None) + "".join(panels))

    froth = m.get("froth") or {}
    pillars = [(k, v) for k, v in (froth.get("pillar_scores") or {}).items() if v is not None]
    if pillars:
        score = froth.get("composite_score")
        label = "Froth pillars, from 0 (calm) to 100 (stretched)."
        if score is not None:
            label += f" Composite {score:.0f} ({froth.get('band', 'n/a')})."
        spacer = '<div style="height:14px;font-size:0;line-height:0">&nbsp;</div>' if parts else ""
        parts.append(spacer + note(label) + _pillar_bars(pillars, details))
    return section("Market dashboard", "".join(parts)) if parts else ""


def _moves(pack: dict, charts: dict | None = None) -> str:
    """Every ticker's one-day move as a bar, largest gain first.

    With *charts* (attached report only) each row opens its 12-month chart.
    """
    rows = [e for e in pack.get("tickers", [])
            if not e.get("failed") and (e.get("moves") or {}).get("ret_1d_pct") is not None]
    if not rows:
        return ""
    rows.sort(key=lambda e: e["moves"]["ret_1d_pct"], reverse=True)
    peak = max(abs(e["moves"]["ret_1d_pct"]) for e in rows) or 1.0
    bars = []
    for e in rows:
        r = e["moves"]["ret_1d_pct"]
        mvn = e["moves"].get("move_vs_normal")
        flag = f"{abs(mvn):.1f}x normal" if mvn is not None and abs(mvn) >= 2 else ""
        bars.append((e["key"], abs(r) / peak, sign_color(r), fmt_pct(r), flag))
    series = (charts or {}).get("tickers") or {}
    if not series:
        body = bar_rows(bars, label_width=48)
    else:
        # One table per row so each can open its chart. A blank flag keeps the
        # flag column, and so the bar widths, the same on every row.
        has_flag = any(b[4] for b in bars)
        out = []
        for e, (label, frac, color, value, flag) in zip(rows, bars):
            row_html = bar_rows([(label, frac, color, value, flag or ("\u00a0" if has_flag else ""))],
                                label_width=48)
            pts = series.get(e["key"])
            if not pts:
                out.append(row_html)
                continue
            pid = f"px-{e['key']}"
            name = f"{e['key']} · {e.get('name') or e['key']}"
            out.append(f'<div class="detail-click" {click_attrs(pid, f"Show {label} over 12 months")}>'
                       f'{row_html}</div><div class="detail-panel" id="{pid}">'
                       f'{series_panel(name, pts, "price", e["moves"])}</div>')
        body = f'<div class="detail-bars">{"".join(out)}</div>'
    return section("Today's moves", body,
                   subtitle="One-day price change. Flagged when a move is 2x or more a normal day.")


def _factor_line(factor: dict | None) -> str:
    """The model score that pushed hardest, e.g. 'Valuation ▲ 41%'."""
    if not factor:
        return ""
    up = factor["direction"] == "up"
    label = f"{factor['label']} {'▲' if up else '▼'} {factor['share_pct']}%"
    return (f'<div style="margin:0 0 6px 0;font-family:{SANS};font-size:12px;color:{MUTED}">'
            f'Main factor {chip(label, GREEN if up else RED, INSET)} '
            f'<span style="color:{FAINT}">{factor["contribution"]:+.2f} of the composite score</span></div>')


def _stories(sections: dict, pack: dict) -> str:
    if not sections.get("top_stories"):
        return ""
    facts = {s["key"]: s for s in pack.get("top_stories", [])}
    cards = []
    for s in sections["top_stories"]:
        f = facts.get(s["ticker"], {})
        r = (f.get("moves") or {}).get("ret_1d_pct")
        action = f.get("action", "")
        move = chip(fmt_pct(r), sign_color(r), INSET) if r is not None else ""
        model = chip(f"model {action}", ACTION_COLORS.get(action, MUTED), INSET) if action else ""
        head = (f'<div style="margin-bottom:6px">{mono(s["ticker"], size=14, weight=600)} {move}'
                f'<span style="float:right">{model}</span></div>')
        title = (f'<div style="font-family:{SANS};font-size:13px;font-weight:500;color:{SUBTLE};'
                 f'margin:0 0 6px 0">{esc(s["title"])}</div>') if s.get("title") else ""
        cards.append(inset(head + _factor_line(f.get("main_factor")) + title + prose(s["body"], size=13)
                           + _source_line(s)))
    subtitle = None
    if any(f.get("main_factor") for f in facts.values()):
        subtitle = ("Main factor: which of the model's six scores (valuation, trend, risk, macro, news, "
                    "volatility) pushed its composite hardest, up or down, and that factor's share of the "
                    "total push.")
    return section("Top stories", "".join(cards), subtitle=subtitle)


def _signal_cell(text: str, color: str, weight: int = 400) -> str:
    return (f'<td style="padding:6px;border-bottom:1px solid {ROW_RULE};font-family:{MONO};'
            f'font-size:12px;font-weight:{weight};color:{color};white-space:nowrap">{esc(text)}</td>')


def _signals(pack: dict) -> str:
    """Shadow earnings signal: what the composite would be with earnings counted."""
    signals = pack.get("earnings_signals") or []
    if not signals:
        return ""
    head = (f"padding:4px 6px;border-bottom:1px solid {BORDER};font-family:{SANS};font-size:10px;"
            f"font-weight:500;color:{FAINT};text-transform:uppercase;letter-spacing:0.5px;text-align:left")
    rows = []
    for s in signals:
        eps = (s.get("surprise") or {}).get("EPS")
        fy1 = (s.get("revisions") or {}).get("FY1")
        score = s.get("score")
        comp, shadow = s.get("composite"), s.get("shadow_composite")
        if comp is not None and shadow is not None:
            flips = s.get("shadow_action") != s.get("action")
            shadow_text = f"{comp:+.2f} → {shadow:+.2f}" + (f" {s['shadow_action']}" if flips else "")
            shadow_color = AMBER if flips else TEXT
        else:
            shadow_text, shadow_color = "n/a", MUTED
        rows.append(
            "<tr>"
            + _signal_cell(s["key"], HEADING, 600)
            + _signal_cell(f"{s['trading_days_since']}d", TEXT)
            + _signal_cell(fmt_pct(eps["pct"]) + ("" if eps["vintage"] == "pre_results" else "*")
                           if eps else "n/a", sign_color(eps["pct"]) if eps else MUTED)
            + _signal_cell(fmt_pct(fy1["pct"]) if fy1 else "n/a", sign_color(fy1["pct"]) if fy1 else MUTED)
            + _signal_cell("n/a" if score is None else f"{score:+.2f}",
                           MUTED if score is None else sign_color(score))
            + _signal_cell(shadow_text, shadow_color)
            + "</tr>")
    labels = ("Ticker", "Since results", "EPS surprise", "FY1 revision", "Earnings score",
              "Composite → with earnings")
    table = ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
             'style="border-collapse:collapse"><tr>'
             + "".join(f'<th style="{head}">{esc(label)}</th>' for label in labels)
             + "</tr>" + "".join(rows) + "</table>")
    subtitle = ("Shadow mode: shown for review, not used in ratings yet. The earnings score would carry "
                "up to 20% of the composite, fading to zero 60 trading days after results. "
                "* = against the latest published consensus, not the pre-results figure.")
    return section("Earnings signal (shadow)", table, subtitle=subtitle)


# LSEG news source codes as readers know them.
SOURCE_NAMES = {"RTRS": "Reuters", "PUBT": "Public Technologies"}


def _source_line(story: dict) -> str:
    """'Sources: Reuters, 7 Oct: headline' under a top story, when cited."""
    cites = []
    for src in story.get("sources") or []:
        try:
            d = dt.date.fromisoformat(src.get("date", ""))
            day = f"{d.day} {d:%b}"
        except ValueError:
            day = ""
        name = SOURCE_NAMES.get(src.get("source", ""), src.get("source"))
        who = ", ".join(x for x in (name, day) if x)
        cites.append(f"{who}: {src['headline']}" if who else src["headline"])
    if not cites:
        return ""
    return (f'<div style="font-family:{SANS};font-size:11px;line-height:1.5;color:{FAINT};'
            f'margin-top:6px">Sources: {esc(" · ".join(cites))}</div>')


def _earnings(sections: dict) -> str:
    items = sections.get("earnings_watch") or []
    if not items:
        return ""
    body = "".join(
        f'<div style="margin:0 0 4px 0">{mono(it["ticker"], size=13, weight=600)} '
        f'<span style="font-family:{SANS};font-size:13px;color:{SUBTLE}">{esc(it.get("title") or "")}'
        f'</span></div>{prose(it["body"], size=13)}' for it in items)
    return section("Earnings watch", body,
                   subtitle="Earnings calls in the last 14 days, from LSEG transcripts.")


def _closest(sections: dict, pack: dict) -> str:
    facts = pack.get("closest_to_changing") or []
    if not facts:
        return ""
    span = float((pack.get("thresholds") or {}).get("buy", 0.25))
    bars = [(c["key"], max(0.0, 1 - c["gap"] / span),
             GREEN if c["nearest_signal"] == "BUY" else RED,
             f"{c['gap']:.2f} to {c['nearest_signal']}", "") for c in facts]
    texts = {it["ticker"]: it["body"] for it in sections.get("closest_to_changing") or []}
    notes = "".join(
        f'<div style="margin-top:8px">{mono(c["key"], size=12, weight=600)} '
        f'<span style="font-family:{SANS};font-size:13px;line-height:1.6;color:{TEXT}">'
        f'{esc(texts[c["key"]])}</span></div>'
        for c in facts if texts.get(c["key"]))
    return section("Closest to changing", bar_rows(bars, label_width=48, value_width=96) + notes,
                   subtitle="HOLDs nearest a signal. A full bar means the score is on the threshold.")


def _source(sections: dict) -> str:
    text = ("Written by Claude from LSEG data in the briefing pack."
            if sections.get("source") == "claude"
            else "Template text. The Claude writing step was unavailable for this run.")
    return f'<div style="font-family:{SANS};font-size:11px;color:{FAINT}">{esc(text)}</div>'


def briefing_blocks(sections: dict, pack: dict, details: dict[str, str] | None = None,
                    charts: dict | None = None) -> dict[str, str]:
    """Each briefing block as standalone HTML; empty string when there is no data.

    *details* and *charts* switch on the attached report's click-throughs.
    """
    return {
        "headline": _headline(sections["headline"]) if sections.get("headline") else "",
        "macro": section("Market & macro", prose(sections["market_macro"])),
        "dashboard": _dashboard(pack, details, charts),
        "moves": _moves(pack, charts),
        "stories": _stories(sections, pack),
        "earnings": _earnings(sections),
        "signals": _signals(pack),
        "closest": _closest(sections, pack),
        "source": _source(sections),
    }


EMAIL_ORDER = ("headline", "macro", "dashboard", "moves", "stories", "earnings", "signals",
               "closest", "source")


def briefing_email_rows(sections: dict, pack: dict) -> str:
    """Email table rows, one block per row, single column for phones."""
    blocks = briefing_blocks(sections, pack)
    return "\n".join(email_row(blocks[k], top=16 if k == "headline" else 12)
                     for k in EMAIL_ORDER if blocks[k])


def briefing_page_html(sections: dict, pack: dict, details: dict[str, str] | None = None,
                       charts: dict | None = None) -> str:
    """Attached-report layout: the same blocks in two-column grids.

    *details* (pillar -> HTML) makes the dashboard's pillar bars clickable;
    *charts* (``charts.json``) does the same for the index tiles and the
    Today's moves rows.
    """
    b = briefing_blocks(sections, pack, details, charts)

    def stack(*keys: str) -> str:
        return "".join(f'<div class="stack">{b[k]}</div>' for k in keys if b[k])

    out = ['<div class="brief">']
    if b["headline"]:
        out.append(f'<div class="stack">{b["headline"]}</div>')
    out.append(f'<div class="grid-2"><div>{stack("macro")}</div><div>{stack("dashboard")}</div></div>')
    out.append(f'<div class="grid-2"><div>{stack("moves")}</div>'
               f'<div>{stack("closest", "earnings")}</div></div>')
    out.append(stack("stories", "signals"))
    out.append(b["source"])
    out.append("</div>")
    return "".join(out)
