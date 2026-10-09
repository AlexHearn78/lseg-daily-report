"""Click-through panels and inline SVG line charts for the attached report.

Mail apps strip scripts, so these only appear in the attached HTML report:
an element with ``detail-click`` and :func:`click_attrs` opens and closes a
hidden ``detail-panel`` by id. Charts are self-contained SVG with native
``<title>`` tooltips, so they need no charting library.
"""
from __future__ import annotations

import datetime as dt
import html
from typing import Any, Callable

from lseg_quant.reporting.theme import (
    BLUE,
    BORDER,
    CARD,
    FAINT,
    INSET,
    MONO,
    MUTED,
    SANS,
    SUBTLE,
    TEXT,
)

# Chart colours: reference categorical slots 1-2 stepped for dark surfaces,
# validated against the house CARD (#081D3F) and INSET (#0C2549) backgrounds.
SERIES_1 = "#3987e5"
SERIES_2 = "#d95926"
_GRID = BORDER
_AXIS_TEXT = MUTED
_INK = TEXT

# Chart widths in viewBox units: narrow panels sit inside a half-width card,
# wide ones span the page.
NARROW = 440
WIDE = 600


def _e(s: Any) -> str:
    return html.escape(str(s), quote=True)


def svg_line_chart(series: list[tuple[str, str, list[list[Any]]]],
                   value_fmt: Callable[[float], str] = lambda v: f"{v:.1f}",
                   y_range: tuple[float, float] | None = None,
                   ref_lines: list[tuple[float, str]] | None = None,
                   title: str = "", width: int = 600, height: int = 150,
                   surface: str = CARD) -> str:
    """Line chart of one or more ``(name, colour, [[date, value], ...])`` series.

    One series: the title names it, no legend. Two or more: a legend row plus
    a direct label at each line's end. Every point carries a hover tooltip.
    """
    series = [(n, c, pts) for n, c, pts in series if pts]
    if not series:
        return ""
    pad_l, pad_r, pad_t, pad_b = 8, 118, 10, 22
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b

    dates = sorted({p[0] for _, _, pts in series for p in pts})
    values = [p[1] for _, _, pts in series for p in pts]
    if y_range:
        lo, hi = y_range
    else:
        lo, hi = min(values), max(values)
        span = (hi - lo) or abs(hi) or 1.0
        lo, hi = lo - span * 0.08, hi + span * 0.08
    # Time-scaled x axis, so gaps in quarterly data show as gaps.
    day = {d: dt.date.fromisoformat(d).toordinal() for d in dates}
    d0, d_span = day[dates[0]], max(day[dates[-1]] - day[dates[0]], 1)

    def x(d: str) -> float:
        return pad_l + pw * (day[d] - d0) / d_span

    def y(v: float) -> float:
        return pad_t + ph * (1 - (v - lo) / (hi - lo))

    parts: list[str] = [
        f'<svg class="detail-chart" viewBox="0 0 {width} {height}" width="100%" '
        f'role="img" aria-label="{_e(title)}" preserveAspectRatio="xMinYMin meet">'
    ]
    # Recessive grid at the min / max of the visible range, labelled on the
    # left so the right margin stays free for end-of-line labels.
    ref_ys = [y(v) for v, _ in ref_lines or [] if lo <= v <= hi]
    for v, dy in ((lo, -4), (hi, 12)):
        parts.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
                     f'stroke="{_GRID}" stroke-width="1"/>')
        if any(abs(y(v) - ry) < 16 for ry in ref_ys):
            continue  # a reference-line label sits here; skip the axis label
        parts.append(f'<text x="{pad_l + 2}" y="{y(v) + dy:.1f}" fill="{_AXIS_TEXT}" '
                     f'font-size="11">{_e(value_fmt(v))}</text>')
    for v, label in ref_lines or []:
        if lo <= v <= hi:
            parts.append(f'<line x1="{pad_l}" x2="{pad_l + pw}" y1="{y(v):.1f}" '
                         f'y2="{y(v):.1f}" stroke="{_AXIS_TEXT}" stroke-width="1" '
                         f'stroke-dasharray="3 3" opacity="0.6"/>')
            parts.append(f'<text x="{pad_l + 2}" y="{y(v) - 4:.1f}" fill="{_AXIS_TEXT}" '
                         f'font-size="11">{_e(label)}</text>')
    # Year ticks along the bottom, skipping any that would collide.
    seen: set[str] = set()
    last_x = -1e9
    for d in dates:
        yr = d[:4]
        if yr in seen or x(d) - last_x < 44:
            seen.add(yr)
            continue
        seen.add(yr)
        last_x = x(d)
        parts.append(f'<text x="{x(d):.1f}" y="{height - 6}" fill="{_AXIS_TEXT}" '
                     f'font-size="11">{yr}</text>')

    for name, color, pts in series:
        # Break the line where data is missing: a gap over 3x the usual
        # spacing, and never for under a week (weekends, holidays).
        steps = sorted(day[b[0]] - day[a[0]] for a, b in zip(pts, pts[1:]))
        max_step = max(3 * steps[len(steps) // 2], 7) if steps else 0
        segs = []
        for i, (d, v) in enumerate(pts):
            gap = i > 0 and day[d] - day[pts[i - 1][0]] > max_step
            segs.append(f'{"M" if i == 0 or gap else "L"}{x(d):.1f},{y(v):.1f}')
        path = " ".join(segs)
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="2" '
                     f'stroke-linejoin="round" stroke-linecap="round"/>')
        ld, lv = pts[-1]
        parts.append(f'<circle cx="{x(ld):.1f}" cy="{y(lv):.1f}" r="4" fill="{color}" '
                     f'stroke="{surface}" stroke-width="2"/>')
        end_label = value_fmt(lv) if len(series) == 1 else f"{name} {value_fmt(lv)}"
        parts.append(f'<text x="{x(ld) + 8:.1f}" y="{y(lv) - 6:.1f}" fill="{_INK}" '
                     f'font-size="11">{_e(end_label)}</text>')

    # Hover targets: one full-height strip per date, wider than the marks.
    by_date: dict[str, list[str]] = {}
    for name, _, pts in series:
        for d, v in pts:
            prefix = f"{name}: " if len(series) > 1 else ""
            by_date.setdefault(d, []).append(f"{prefix}{value_fmt(v)}")
    xs = [x(d) for d in dates]
    for i, d in enumerate(dates):
        left = pad_l if i == 0 else (xs[i - 1] + xs[i]) / 2
        right = pad_l + pw if i == len(dates) - 1 else (xs[i] + xs[i + 1]) / 2
        tip = f"{d}  " + "  ".join(by_date.get(d, []))
        parts.append(f'<rect x="{left:.1f}" y="{pad_t}" width="{max(right - left, 1):.1f}" '
                     f'height="{ph}" fill="transparent"><title>{_e(tip)}</title></rect>')
    parts.append("</svg>")

    legend = ""
    if len(series) > 1:
        legend = '<div class="detail-legend">' + "".join(
            f'<span><i style="background:{c}"></i>{_e(nm)}</span>' for nm, c, _ in series
        ) + "</div>"
    head = f'<div class="detail-chart-title">{_e(title)}</div>' if title else ""
    return f'<div class="detail-chart-wrap">{head}{legend}{"".join(parts)}</div>'


CLICK_CSS = f"""
/* One table per pillar row; keep the single bottom gap the page gives one table. */
.detail-bars {{ margin-bottom: 20px; }}
.detail-bars > table, .detail-bars > .detail-click > table {{ margin-bottom: 0; }}
.detail-click {{ position: relative; cursor: pointer; border-radius: 4px; }}
.detail-click:hover {{ background: rgba(46, 107, 255, 0.10); }}
.detail-click:focus-visible {{ outline: 2px solid {BLUE}; outline-offset: 2px; }}
.detail-click::before {{ content: '\\25B8'; position: absolute; left: -12px; top: 50%;
  transform: translateY(-50%); color: {FAINT}; font-size: 10px; }}
.detail-click.open::before {{ content: '\\25BE'; }}
.stat-card.detail-click::before, td.detail-click::before {{ left: auto; right: 8px; top: 10px;
  transform: none; }}
.detail-panel {{ display: none; background: {INSET}; border-radius: 6px; padding: 12px 14px;
  margin: 4px 0 10px 0; }}
.detail-panel.open {{ display: block; }}
.detail-panel.wide {{ background: {CARD}; border-radius: 8px; padding: 16px;
  margin: -8px 0 20px 0; }}
.detail-charts {{ display: grid; grid-template-columns: repeat(auto-fill,
  minmax(min(100%, 460px), 1fr)); gap: 12px 24px; }}
.detail-chart-title {{ font-family: {SANS}; font-size: 11px; color: {SUBTLE};
  text-transform: uppercase; letter-spacing: 0.5px; margin: 6px 0 2px 0; }}
.detail-legend {{ font-family: {SANS}; font-size: 11px; color: {TEXT}; margin-bottom: 2px; }}
.detail-legend span {{ margin-right: 14px; }}
.detail-legend i {{ display: inline-block; width: 10px; height: 3px; border-radius: 2px;
  vertical-align: middle; margin-right: 5px; }}
.detail-chart text {{ font-family: {MONO}; }}
"""

CLICK_JS = """
function toggleDetail(el, id) {
  var panel = document.getElementById(id);
  if (!panel) return;
  panel.classList.toggle('open');
  el.classList.toggle('open');
  el.setAttribute('aria-expanded', panel.classList.contains('open'));
}
function toggleDetailKey(event, el, id) {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggleDetail(el, id); }
}
"""


def click_attrs(panel_id: str, hint: str) -> str:
    """Attributes that make an element open and close *panel_id*.

    The element also needs the ``detail-click`` class.
    """
    return (f'role="button" tabindex="0" aria-expanded="false" '
            f'aria-controls="{panel_id}" title="{_e(hint)}" '
            f'onclick="toggleDetail(this, \'{panel_id}\')" '
            f'onkeydown="toggleDetailKey(event, this, \'{panel_id}\')"')
