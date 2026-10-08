"""Offline tests for the shared report theme helpers."""
from __future__ import annotations

from lseg_quant.reporting import theme


def test_tint_blends_over_base() -> None:
    assert theme.tint("#FFFFFF", 0.5, "#000000") == "#808080"
    assert theme.tint("#34C759", 0.0) == theme.CARD


def test_bar_clamps_fill() -> None:
    assert 'width="1%"' in theme.bar(0.0, theme.GREEN)
    full = theme.bar(1.7, theme.GREEN)
    assert 'width="100%"' in full and "&nbsp;</td><td" not in full


def test_froth_color_bands() -> None:
    assert theme.froth_color(85) == theme.RED
    assert theme.froth_color(65) == theme.AMBER
    assert theme.froth_color(10) == theme.GREEN
    assert theme.froth_color(45) == theme.BLUE
    assert theme.froth_color(None) == theme.MUTED


def test_stat_tiles_equal_widths_and_escaping() -> None:
    html = theme.stat_tiles([("A", "1", theme.GREEN, None), ("B<", "2", theme.RED, "sub")])
    assert html.count('width="50%"') == 2
    assert "B&lt;" in html


def test_email_document_declares_dark_scheme() -> None:
    doc = theme.email_document("t", "<tr><td>x</td></tr>")
    assert 'content="dark"' in doc and f'bgcolor="{theme.PAGE}"' in doc
