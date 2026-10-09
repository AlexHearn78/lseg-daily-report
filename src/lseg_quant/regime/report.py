"""Click-through detail behind the froth score and market regime.

The attached MDU report shows the froth pillars as bars in the Market
dashboard card and FROTH / MACRO REGIME as stat tiles. This module renders
what opens when one is clicked: each pillar's metrics with raw values, data
dates, plain-English notes and history charts; how the pillars add up to the
composite; and the regime rules with today's readings. Input is a
``froth_score.json`` payload from ``workflows/regime_froth.py``; payloads
written before raw values and ``history`` were saved still render, with
scores only. The email is unchanged (mail apps strip scripts).
"""
from __future__ import annotations

import html
import logging
from dataclasses import dataclass
from typing import Any, Callable

from lseg_quant.regime.score import PILLAR_WEIGHTS
from lseg_quant.reporting.clickthrough import (
    NARROW,
    SERIES_1,
    SERIES_2,
    WIDE,
    svg_line_chart,
)
from lseg_quant.reporting.theme import (
    AMBER,
    BORDER,
    CARD,
    FAINT,
    HEADING,
    INSET,
    MONO,
    MUTED,
    ROW_RULE,
    SANS,
    TEXT,
    froth_color,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricInfo:
    """How to label, format and explain one froth metric."""

    label: str
    source: str
    fmt: Callable[[float], str]
    what: str
    why: str
    caveat: str = ""


METRIC_INFO: dict[str, MetricInfo] = {
    "spx_stretch": MetricInfo(
        label="S&P 500 stretch",
        source="LSEG .SPX daily close",
        fmt=lambda v: f"{v:+.1f}%",
        what="How far the S&P 500 sits above or below its 200-day average.",
        why="Prices far above their own trend mean buyers have run ahead, so a "
            "HIGH stretch scores as frothy.",
        caveat="A price measure standing in for valuation: no licensed credit-spread "
               "or index-valuation history was available through LSEG for this pillar.",
    ),
    "lseg_spx_skew": MetricInfo(
        label="S&P 500 put skew",
        source="LSEG SPX volatility surface, ~3-month expiry",
        fmt=lambda v: f"{v:.2f} vol pts",
        what="Implied volatility of 90% strike options minus at-the-money options: the "
             "price of crash protection.",
        why="Cheap protection means few investors are hedging, so a LOW skew scores "
            "as frothy.",
    ),
    "eurex_putcall_sx5e": MetricInfo(
        label="Euro Stoxx 50 put/call ratio",
        source="LSEG .PRSTXE.EX (Eurex), daily",
        fmt=lambda v: f"{v:.2f}",
        what="Volume of Euro Stoxx 50 index puts traded on Eurex divided by calls.",
        why="Few puts relative to calls means little hedging, so a LOW ratio scores "
            "as frothy.",
        caveat="A single day's ratio is noisy; it ranks against ten years of daily values.",
    ),
    "funding_spread": MetricInfo(
        label="Funding spread (SOFR - EFFR)",
        source="LSEG USDSOFR= and USONFFE=FEDR fixings (New York Fed), daily",
        fmt=lambda v: f"{v * 100:+.0f} bp",
        what="Secured overnight rate minus the effective fed funds rate: how tight "
             "short-term dollar funding is.",
        why="A low or negative spread means cash is easy to borrow, so a LOW spread "
            "scores as frothy.",
    ),
    "real_policy_rate": MetricInfo(
        label="Real policy rate",
        source="LSEG effective fed funds fixing minus US CPI YoY (qa_macroeconomic USCONPRCE)",
        fmt=lambda v: f"{v:+.2f}%",
        what="The overnight policy rate after inflation.",
        why="A low or negative real rate means money is loose, so a LOW rate scores "
            "as frothy.",
        caveat="Each CPI print is used from about 45 days after its month, when it "
               "is actually published.",
    ),
}

PILLAR_QUESTION: dict[str, str] = {
    "valuation": "How much are investors paying for risk?",
    "positioning": "How crowded and unhedged are investors?",
    "liquidity": "How easy and cheap is money?",
}

STRUCTURAL = ("valuation",)
TIMING = ("positioning", "liquidity")


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def _score(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f}"


def _metric_value(name: str, value: float | None) -> str:
    if value is None:
        return "—"
    info = METRIC_INFO.get(name)
    return info.fmt(value) if info else f"{value:,.4g}"


def _label(name: str) -> str:
    info = METRIC_INFO.get(name)
    return info.label if info else name.replace("_", " ")


def _pillar_metrics(ctx: dict[str, Any], pillar: str) -> list[tuple[str, dict[str, Any]]]:
    rows = [(n, m) for n, m in (ctx.get("metrics") or {}).items() if m.get("pillar") == pillar]
    return sorted(rows, key=lambda r: -(r[1].get("score") or -1))


def _year(date: str | None) -> str:
    return date[:4] if date else "?"


def _p(text: str, color: str = TEXT, size: int = 13) -> str:
    """Body paragraph; *text* must already be escaped."""
    return (f'<p style="font-family:{SANS};font-size:{size}px;line-height:1.55;color:{color};'
            f'margin:0 0 8px 0">{text}</p>')


def _b(text: str) -> str:
    return f'<b style="color:{HEADING};font-weight:600">{_e(text)}</b>'


_BAND_LINES = [(80.0, "frothy 80"), (20.0, "capitulation 20")]



def _score_chart(points: list[list[Any]], title: str, width: int, surface: str) -> str:
    return svg_line_chart([("score", SERIES_1, points)], y_range=(0.0, 100.0),
                          ref_lines=_BAND_LINES, title=title, width=width, surface=surface)


def _table(head: list[str], rows: list[list[str]], numeric: set[int]) -> str:
    """Small house-style table; cells must already be escaped HTML."""
    th = "".join(
        f'<th align="{"right" if i in numeric else "left"}" style="font-family:{SANS};'
        f'font-size:11px;font-weight:500;color:{MUTED};text-transform:uppercase;'
        f'letter-spacing:0.5px;padding:6px 8px;border-bottom:1px solid {BORDER}">{_e(h)}</th>'
        for i, h in enumerate(head))
    body = "".join(
        "<tr>" + "".join(
            f'<td align="{"right" if i in numeric else "left"}" style="font-family:{MONO};'
            f'font-size:12px;color:{TEXT};padding:6px 8px;border-bottom:1px solid {ROW_RULE}">'
            f'{cell}</td>' for i, cell in enumerate(row)) + "</tr>"
        for row in rows)
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="margin:4px 0 12px 0"><tr>{th}</tr>{body}</table>')


# ---------------------------------------------------------------------------
# Pillar drilldown (opens under its bar in the Market dashboard card)
# ---------------------------------------------------------------------------

def _metric_block(name: str, m: dict[str, Any], history: list[list[Any]]) -> str:
    info = METRIC_INFO.get(name)
    value = _metric_value(name, m.get("value"))
    score = m.get("score")
    head = (f'<div style="display:flex;justify-content:space-between;gap:8px;'
            f'align-items:baseline;margin-bottom:2px">'
            f'<span style="font-family:{SANS};font-size:13px;font-weight:600;color:{HEADING}">'
            f'{_e(_label(name))}</span>'
            f'<span style="font-family:{MONO};font-size:13px;color:{HEADING};white-space:nowrap">'
            f'{_e(value)} &middot; <span style="color:{froth_color(score)}">score '
            f'{_score(score)}</span></span></div>')
    facts = []
    if m.get("last_date"):
        facts.append(f"data {m['last_date']}")
    if "method" in m:
        facts.append(f'{_year(m.get("history_start"))}–{_year(m.get("last_date"))}, '
                     f'{m.get("n_obs", 0):,} obs')
        facts.append(f'percentile vs last {m.get("window_years", 12):g}y'
                     if m["method"] == "percentile" else "min-max (short history)")
        facts.append(f'frothy when {"low" if m.get("inverted") else "high"}')
    facts_html = (f'<div style="font-family:{MONO};font-size:11px;color:{FAINT};'
                  f'margin-bottom:6px">{_e(" · ".join(facts))}</div>') if facts else ""
    notes = ""
    if info:
        notes = _p(_e(f"{info.what} {info.why}"), size=12)
        if info.caveat:
            notes += _p(_e(f"Caveat: {info.caveat}"), color=AMBER, size=12)
        notes += _p(_e(f"Source: {info.source}."), color=FAINT, size=11)
    fmt = info.fmt if info else (lambda v: f"{v:,.4g}")
    chart = svg_line_chart([(name, SERIES_1, history)], value_fmt=fmt,
                           title=f"{_label(name)}, raw", width=NARROW, surface=INSET)
    return (f'<div style="border-top:1px solid {BORDER};padding-top:10px;margin-top:10px">'
            f'{head}{facts_html}{notes}{chart}</div>')


def pillar_detail_html(ctx: dict[str, Any], pillar: str) -> str:
    """What sits behind one pillar score: its metrics, notes and charts."""
    rows = _pillar_metrics(ctx, pillar)
    score = (ctx.get("pillar_scores") or {}).get(pillar)
    hist = ctx.get("history") or {}
    n = len(rows)
    parts = [_p(
        f'{_b(pillar.title() + " " + _score(score))} is the average of {n} metric '
        f'score{"s" if n != 1 else ""}. It asks: {_e(PILLAR_QUESTION.get(pillar, ""))} '
        'Each metric is ranked 0 to 100 against its own history, where 100 is the most '
        'frothy reading in the window.')]
    if pillar in (ctx.get("low_confidence_pillars") or []):
        parts.append(_p("Low confidence: at least one metric has under three years of "
                        "history.", color=AMBER, size=12))
    if rows and "method" not in rows[0][1]:
        parts.append(_p("Raw values and history were not saved for this date. Runs of "
                        "workflows/regime_froth.py from now on include them.",
                        color=FAINT, size=12))
    parts.append(_score_chart((hist.get("pillars") or {}).get(pillar, []),
                              f"{pillar} score, 10y", NARROW, INSET))
    for name, m in rows:
        parts.append(_metric_block(name, m, (hist.get("metrics") or {}).get(name, [])))
    return "".join(parts)


# ---------------------------------------------------------------------------
# FROTH and MACRO REGIME tile drilldowns (full width under the stat tiles)
# ---------------------------------------------------------------------------

def froth_detail_html(ctx: dict[str, Any]) -> str:
    """How the three pillars add up to the composite, and the structural/timing split."""
    pillars = ctx.get("pillar_scores") or {}
    present = {p: s for p, s in pillars.items() if s is not None}
    w_sum = sum(PILLAR_WEIGHTS[p] for p in present) or 1.0
    rows = [[_e(p.title()),
             f'<span style="color:{froth_color(pillars.get(p))}">{_score(pillars.get(p))}</span>',
             f"{w:.0%}",
             "—" if pillars.get(p) is None else f"{w * pillars[p] / w_sum:.1f}",
             _e("structural" if p in STRUCTURAL else "timing")]
            for p, w in PILLAR_WEIGHTS.items()]
    missing = [p for p in PILLAR_WEIGHTS if p not in present]
    velocity = str(ctx.get("velocity_flag", "n/a")).replace("_", " ")
    hist = ctx.get("history") or {}
    charts = [
        _score_chart(hist.get("composite", []), "composite score, 10y", WIDE, CARD),
        svg_line_chart([("structural", SERIES_1, hist.get("structural", [])),
                        ("timing", SERIES_2, hist.get("timing", []))],
                       y_range=(0.0, 100.0), ref_lines=_BAND_LINES,
                       title="structural vs timing, 10y", width=WIDE, surface=CARD),
    ]
    p = pillars
    comp = _score(ctx.get("composite_score"))
    struct = _score(ctx.get("structural_score"))
    timing = _score(ctx.get("timing_score"))
    return "".join([
        _p(f'{_b(f"Froth {comp}")} is a weighted '
           'average of the three pillars. Bands: under 20 capitulation, 20 to 40 risk off, '
           '40 to 60 balanced, 60 to 80 elevated, 80 and over frothy. Velocity compares '
           'today with about 90 days ago and flags a move of more than 15 points; today '
           f'it reads {_b(velocity)}.'),
        _table(["Pillar", "Score", "Weight", "Points", "Group"], rows, {1, 2, 3}),
        _p("No data for " + _e(", ".join(missing)) + "; the other weights were scaled up "
           "to sum to 100%.", color=FAINT, size=12) if missing else "",
        _p(f'{_b(f"Structural {struct}")} is the '
           f'valuation pillar ({_score(p.get("valuation"))}). It moves slowly and says how '
           f'stretched the market is. {_b(f"Timing {timing}")} is the '
           f'average of positioning ({_score(p.get("positioning"))}) and liquidity '
           f'({_score(p.get("liquidity"))}). These move faster and say whether the '
           'conditions that let froth keep building are still in place.'),
        f'<div class="detail-charts">{"".join(c for c in charts if c)}</div>',
    ])


def _regime_rules(rg: dict[str, Any]) -> list[tuple[str, str, bool | None]]:
    """(rule, current reading, met?) mirroring ``compute_market_regime``."""
    t3, t12 = rg.get("trend_3m"), rg.get("trend_12m")
    curve, stress = rg.get("spread_2s10s"), rg.get("funding_stress_pctile")

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:+.1%}"

    def crv() -> str:
        return "n/a" if curve is None else f"{curve:+.2f}"

    return [
        ("Risk off: S&P 500 3-month return at or below -3%", f"3m {pct(t3)}",
         None if t3 is None else t3 <= -0.03),
        ("Risk off: funding stress in the top 10% of its 10y range and 2s10s at or below 0.5",
         "n/a" if stress is None else f"stress p{stress:.0f}, 2s10s {crv()}",
         None if stress is None else stress >= 90 and (curve is None or curve <= 0.5)),
        ("Risk off: inverted 2s10s and 3-month return at or below -1%",
         f"2s10s {crv()}, 3m {pct(t3)}",
         None if curve is None or t3 is None else curve < 0 and t3 <= -0.01),
        ("Risk on, if no risk-off rule fires: 12-month return above +5% and curve not "
         "inverted", f"12m {pct(t12)}, 2s10s {crv()}",
         None if t12 is None else t12 > 0.05 and (curve is None or curve > 0)),
    ]


def regime_detail_html(ctx: dict[str, Any]) -> str:
    """The risk-on/risk-off rules with today's readings, plus trend and curve charts."""
    rg = ctx.get("market_regime") or {}
    hist = ctx.get("history") or {}
    rows = [[f'<span style="font-family:{SANS};font-size:12px">{_e(rule)}</span>', _e(reading),
             f'<span style="color:{AMBER if met else FAINT}">'
             f'{"n/a" if met is None else "met" if met else "not met"}</span>']
            for rule, reading, met in _regime_rules(rg)]
    charts = [
        svg_line_chart([("S&P 500", SERIES_1, hist.get("spx_close", []))],
                       value_fmt=lambda v: f"{v:,.0f}", title="S&P 500, weekly, 2y",
                       width=WIDE, surface=CARD),
        svg_line_chart([("2s10s", SERIES_1, hist.get("spread_2s10s", []))],
                       value_fmt=lambda v: f"{v:+.2f}", ref_lines=[(0.0, "inverted below")],
                       title="10y minus 2y Treasury yield, 10y", width=WIDE, surface=CARD),
    ]
    charts = [c for c in charts if c]
    reasons = "; ".join(rg.get("reasons") or []) or "none recorded"
    regime = str(rg.get("regime", "n/a")).replace("_", " ")
    return "".join([
        _p(f'{_b("Regime: " + regime)}. This is separate from the froth score: it reads the '
           "market's trend and stress signals today, not how stretched it is. The rules "
           'are checked in order and risk off wins.'),
        _table(["Rule", "Today", "Status"], rows, set()),
        _p(_e(f"Engine reason: {reasons}"), color=FAINT, size=12),
        f'<div class="detail-charts">{"".join(charts)}</div>' if charts else "",
    ])


# ---------------------------------------------------------------------------
# Page hooks: CSS and the toggle script for the attached report
# ---------------------------------------------------------------------------
