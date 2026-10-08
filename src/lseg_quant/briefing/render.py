"""Briefing sections: validation, template fallback and house-style HTML.

``sections.json`` (written by the Claude step) is validated against the pack;
when it is missing or malformed the deterministic template below is used, so
the email always carries a market section. The HTML blocks use
``lseg_quant.reporting.theme`` so the email body and the attached report
share one look.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from lseg_quant.reporting.theme import (
    ACTION_COLORS,
    BLUE,
    FAINT,
    GREEN,
    HEADING,
    INSET,
    MUTED,
    RED,
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


def _text(value: Any, limit: int) -> str:
    """Whitespace-normalised text that keeps blank-line paragraph breaks."""
    if not isinstance(value, str):
        return ""
    paras = [" ".join(p.split()) for p in value.split("\n\n")]
    return "\n\n".join(p for p in paras if p)[:limit]


def validate_sections(raw: Any, pack: dict) -> dict | None:
    """Cleaned sections, or None when the shape is unusable."""
    if not isinstance(raw, dict):
        return None
    macro = _text(raw.get("market_macro"), 1600)
    if not macro:
        return None
    known = {e["key"] for e in pack.get("tickers", [])}

    def items(name: str, limit: int, cap: int) -> list[dict]:
        out: list[dict] = []
        for it in raw.get(name) or []:
            if not isinstance(it, dict):
                continue
            ticker = str(it.get("ticker", "")).upper()
            body = _text(it.get("body"), limit)
            if ticker in known and body:
                out.append({"ticker": ticker, "title": _text(it.get("title"), 120), "body": body})
        return out[:cap]

    return {
        "source": "claude",
        "headline": _text(raw.get("headline"), 240),
        "market_macro": macro,
        "top_stories": items("top_stories", 900, 3),
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
    if sec_path.is_file():
        try:
            cleaned = validate_sections(json.loads(sec_path.read_text()), pack)
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


def _dashboard(pack: dict) -> str:
    """Index, rates and credit tiles, then the four froth pillars as bars."""
    m = pack.get("macro") or {}
    tiles: list[tuple[str, str, str, str | None]] = []
    spx = m.get("sp500") or {}
    if spx.get("ret_1d_pct") is not None:
        tiles.append(("S&P 500 1D", fmt_pct(spx["ret_1d_pct"]), sign_color(spx["ret_1d_pct"]),
                      f"1M {fmt_pct(spx.get('ret_1m_pct'))}"))
    y10 = m.get("us_10y_yield") or {}
    if y10.get("last_pct") is not None:
        tiles.append(("US 10Y", f"{y10['last_pct']:.2f}%", HEADING, _bp(y10.get("chg_1m_bp"))))
    curve = m.get("curve_2s10s_bp")
    if curve is not None:
        tiles.append(("2s10s", f"{curve:+.0f}bp", HEADING if curve >= 0 else RED, None))
    hy = m.get("high_yield_spread") or {}
    if hy.get("last_pct") is not None:
        tiles.append(("HY spread", f"{hy['last_pct']:.2f}%", HEADING, _bp(hy.get("chg_1m_bp"))))
    parts = [stat_tiles(tiles, size=16, base=INSET)] if tiles else []

    froth = m.get("froth") or {}
    pillars = [(k, v) for k, v in (froth.get("pillar_scores") or {}).items() if v is not None]
    if pillars:
        score = froth.get("composite_score")
        label = "Froth pillars, from 0 (calm) to 100 (stretched)."
        if score is not None:
            label += f" Composite {score:.0f} ({froth.get('band', 'n/a')})."
        spacer = '<div style="height:14px;font-size:0;line-height:0">&nbsp;</div>' if parts else ""
        parts.append(spacer + note(label) + bar_rows(
            [(k.capitalize(), v / 100, froth_color(v), f"{v:.0f}", "") for k, v in pillars],
            label_width=84))
    return section("Market dashboard", "".join(parts)) if parts else ""


def _moves(pack: dict) -> str:
    """Every ticker's one-day move as a bar, largest gain first."""
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
    return section("Today's moves", bar_rows(bars, label_width=48),
                   subtitle="One-day price change. Flagged when a move is 2x or more a normal day.")


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
        cards.append(inset(head + title + prose(s["body"], size=13)))
    return section("Top stories", "".join(cards))


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


def briefing_blocks(sections: dict, pack: dict) -> dict[str, str]:
    """Each briefing block as standalone HTML; empty string when there is no data."""
    return {
        "headline": _headline(sections["headline"]) if sections.get("headline") else "",
        "macro": section("Market & macro", prose(sections["market_macro"])),
        "dashboard": _dashboard(pack),
        "moves": _moves(pack),
        "stories": _stories(sections, pack),
        "earnings": _earnings(sections),
        "closest": _closest(sections, pack),
        "source": _source(sections),
    }


EMAIL_ORDER = ("headline", "macro", "dashboard", "moves", "stories", "earnings", "closest", "source")


def briefing_email_rows(sections: dict, pack: dict) -> str:
    """Email table rows, one block per row, single column for phones."""
    blocks = briefing_blocks(sections, pack)
    return "\n".join(email_row(blocks[k], top=16 if k == "headline" else 12)
                     for k in EMAIL_ORDER if blocks[k])


def briefing_page_html(sections: dict, pack: dict) -> str:
    """Attached-report layout: the same blocks in two-column grids."""
    b = briefing_blocks(sections, pack)

    def stack(*keys: str) -> str:
        return "".join(f'<div class="stack">{b[k]}</div>' for k in keys if b[k])

    out = ['<div class="brief">']
    if b["headline"]:
        out.append(f'<div class="stack">{b["headline"]}</div>')
    out.append(f'<div class="grid-2"><div>{stack("macro")}</div><div>{stack("dashboard")}</div></div>')
    out.append(f'<div class="grid-2"><div>{stack("moves")}</div>'
               f'<div>{stack("closest", "earnings")}</div></div>')
    out.append(stack("stories"))
    out.append(b["source"])
    out.append("</div>")
    return "".join(out)
