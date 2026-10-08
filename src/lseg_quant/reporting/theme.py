"""House report style, shared by the MDU email, its attached report and
future reports.

Palette and structure follow the weekly value screen: navy page, lighter
navy cards, sans-serif text, monospace numbers, and horizontal bars instead
of lists. Every helper returns inline-styled HTML built from tables and
divs, so the same markup renders in Gmail (which drops web fonts and most
layout CSS) and in the attached browser report.
"""
from __future__ import annotations

import html

# Palette (weekly value screen)
PAGE = "#03102A"
CARD = "#081D3F"
INSET = "#0C2549"      # tiles and sub-cards inside a card
BORDER = "#1A2F55"
ROW_RULE = "#0E1E3D"
TRACK = "#1A2F55"
TEXT = "#C8D0DC"
HEADING = "#E8EDF5"
SUBTLE = "#8FA3CC"
MUTED = "#6B86B0"
FAINT = "#4A6A94"
GREEN = "#34C759"
RED = "#FF453A"
AMBER = "#FFB547"
CYAN = "#4FD8EB"
BLUE = "#2E6BFF"

SANS = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
MONO = "ReportMono,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace"

ACTION_COLORS = {"BUY": GREEN, "SELL": RED, "HOLD": AMBER}


def esc(value: object) -> str:
    return html.escape(str(value))


def fmt_pct(x: float | None, digits: int = 1) -> str:
    return "n/a" if x is None else f"{x:+.{digits}f}%"


def sign_color(x: float | None) -> str:
    if x is None:
        return MUTED
    return GREEN if x >= 0 else RED


def froth_color(score: float | None) -> str:
    """Colour for a 0-100 froth reading, where high means stretched."""
    if score is None:
        return MUTED
    if score >= 80:
        return RED
    if score >= 60:
        return AMBER
    if score <= 20:
        return GREEN
    return BLUE


def tint(color: str, alpha: float = 0.2, base: str = CARD) -> str:
    """Solid blend of *color* over *base*; email clients mishandle rgba()."""
    c = [int(color[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(base[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(b[k] + (c[k] - b[k]) * alpha):02X}" for k in range(3))


# ---------------------------------------------------------------------------
# Inline pieces
# ---------------------------------------------------------------------------

def mono(text: object, color: str = HEADING, size: int = 13, weight: int = 500) -> str:
    return (f'<span style="font-family:{MONO};font-size:{size}px;font-weight:{weight};'
            f'color:{color}">{esc(text)}</span>')


def chip(text: object, color: str, base: str = CARD) -> str:
    return (f'<span style="display:inline-block;font-family:{MONO};font-size:11px;'
            f'font-weight:600;color:{color};background:{tint(color, 0.2, base)};'
            f'border-radius:3px;padding:1px 6px;white-space:nowrap">{esc(text)}</span>')


def prose(text: str, size: int = 14) -> str:
    """Paragraphs split on blank lines."""
    return "".join(
        f'<p style="margin:0 0 10px 0;font-family:{SANS};font-size:{size}px;'
        f'line-height:1.6;color:{TEXT}">{esc(p)}</p>'
        for p in text.split("\n\n") if p.strip())


def note(text: str) -> str:
    return (f'<div style="font-family:{SANS};font-size:11px;color:{MUTED};'
            f'margin:0 0 10px 0">{esc(text)}</div>')


# ---------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------

def section(title: str, body: str, subtitle: str | None = None) -> str:
    """A navy card with a ruled title, like the value screen's section cards."""
    return (f'<div style="background:{CARD};border-radius:8px;padding:16px">'
            f'<div style="font-family:{SANS};font-size:16px;font-weight:500;color:{HEADING};'
            f'border-bottom:1px solid {BORDER};padding-bottom:6px;margin-bottom:12px">'
            f'{esc(title)}</div>{note(subtitle) if subtitle else ""}{body}</div>')


def inset(body: str, margin_bottom: int = 10) -> str:
    """A lighter panel inside a section."""
    return (f'<div style="background:{INSET};border-radius:6px;padding:12px 14px;'
            f'margin:0 0 {margin_bottom}px 0">{body}</div>')


def stat_tiles(tiles: list[tuple[str, str, str, str | None]], size: int = 20,
               base: str = CARD) -> str:
    """Equal-width tiles of (label, value, colour, sub-line or None)."""
    width = f"{100 / len(tiles):.0f}%"
    cells = []
    for label, value, color, sub in tiles:
        sub_html = (f'<div style="font-family:{MONO};font-size:11px;color:{FAINT};'
                    f'margin-top:1px;white-space:nowrap">{esc(sub)}</div>') if sub else ""
        cells.append(
            f'<td width="{width}" style="background:{base};border-radius:8px;'
            f'padding:10px 12px;vertical-align:top">'
            f'<div style="font-family:{MONO};font-size:{size}px;font-weight:600;color:{color};'
            f'white-space:nowrap">{esc(value)}</div>'
            f'<div style="font-family:{SANS};font-size:11px;color:{MUTED};text-transform:uppercase;'
            f'letter-spacing:0.5px;margin-top:2px">{esc(label)}</div>{sub_html}</td>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:separate;border-spacing:6px 0">'
            f'<tr>{"".join(cells)}</tr></table>')


def bar(fraction: float, color: str, height: int = 12) -> str:
    """A filled track; the fill never drops below 1% so zero stays visible."""
    pct = max(1, min(100, round(fraction * 100)))
    rest = '<td style="font-size:0;line-height:0">&nbsp;</td>' if pct < 100 else ""
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            f'style="background:{TRACK};border-radius:4px"><tr>'
            f'<td width="{pct}%" style="background:{color};height:{height}px;border-radius:4px;'
            f'font-size:0;line-height:0">&nbsp;</td>{rest}</tr></table>')


def bar_rows(rows: list[tuple[str, float, str, str, str]], label_width: int = 64,
             value_width: int = 60) -> str:
    """Horizontal bar chart of (label, fraction 0-1, colour, value text, flag)."""
    has_flag = any(r[4] for r in rows)
    out = []
    for label, fraction, color, value, flag in rows:
        flag_td = (f'<td width="86" style="padding:3px 0 3px 8px;font-family:{MONO};'
                   f'font-size:11px;color:{AMBER};white-space:nowrap">{esc(flag)}</td>'
                   ) if has_flag else ""
        out.append(
            f'<tr><td width="{label_width}" style="padding:3px 8px 3px 0;font-family:{MONO};'
            f'font-size:12px;color:{HEADING};white-space:nowrap">{esc(label)}</td>'
            f'<td style="padding:3px 0">{bar(fraction, color)}</td>'
            f'<td width="{value_width}" align="right" style="padding:3px 0 3px 8px;'
            f'font-family:{MONO};font-size:12px;color:{color};white-space:nowrap">'
            f'{esc(value)}</td>{flag_td}</tr>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0">'
            f'{"".join(out)}</table>')


# ---------------------------------------------------------------------------
# Email document
# ---------------------------------------------------------------------------

def email_row(block: str, top: int = 12) -> str:
    return f'<tr><td style="padding:{top}px 0 0 0">{block}</td></tr>'


def email_document(title: str, rows_html: str) -> str:
    """Navy email shell. The colour-scheme hints stop mail apps inverting it."""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark">
<meta name="supported-color-schemes" content="dark">
<title>{esc(title)}</title>
</head>
<body style="margin:0;padding:0;background:{PAGE}" bgcolor="{PAGE}">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" bgcolor="{PAGE}" style="background:{PAGE}">
<tr><td align="center" style="padding:20px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:680px">
{rows_html}
</table>
</td></tr>
</table>
</body>
</html>"""
