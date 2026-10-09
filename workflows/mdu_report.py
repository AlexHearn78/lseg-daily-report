#!/usr/bin/env python3
"""Daily MDU HTML report — reads audit JSONL, produces one self-contained page.

Usage:
    uv run workflows/mdu_report.py
    uv run workflows/mdu_report.py --date 2026-07-02
    uv run workflows/mdu_report.py --date 2026-07-02 --out-dir /tmp/reports

Reads only from the audit trail — never recomputes signals or calls MCP.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import logging
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lseg_quant.mdu.config import UNIVERSE, load_report_config
from lseg_quant.briefing.render import (
    briefing_email_rows,
    briefing_page_html,
    load_briefing,
    load_charts,
)
from lseg_quant.regime.daily import load_latest_context
from lseg_quant.regime.report import (
    froth_detail_html,
    pillar_detail_html,
    regime_detail_html,
)
from lseg_quant.regime.score import PILLAR_WEIGHTS
from lseg_quant.reporting.clickthrough import CLICK_CSS, CLICK_JS, click_attrs
from lseg_quant.reporting import theme as house

logger = logging.getLogger("mdu_report")

ALL_TICKER_KEYS = list(UNIVERSE.keys())
TICKER_ORDER = {k: i for i, k in enumerate(ALL_TICKER_KEYS)}
BRIEFING_ROOT = Path("data/outputs/briefing")

_REPORT = load_report_config()
REPORT_TITLE: str = _REPORT["title"]
SEND_TZ = ZoneInfo(_REPORT["timezone"])
SEND_TZ_LABEL: str = _REPORT["timezone_label"]


def _short_date(d: dt.date) -> str:
    """Header date, e.g. '11 Sep 26'."""
    return f"{d.day} {d:%b %y}"


def _send_stamp(now: dt.datetime | None = None) -> str:
    """Send/generation time in the report timezone, e.g. '13 Sep 26 – 13:48 (London)'."""
    local = (now or dt.datetime.now(dt.timezone.utc)).astimezone(SEND_TZ)
    return f"{_short_date(local)} – {local:%H:%M} ({SEND_TZ_LABEL})"


def _briefing(date_str: str) -> tuple[dict | None, dict | None]:
    """(sections, pack) for the run's briefing, or (None, None) when none was built."""
    return load_briefing(BRIEFING_ROOT / date_str.replace("-", ""))


PACK_SCRIPT_ID = "briefing-pack"


def _pack_block(pack: dict | None) -> str:
    """The briefing pack as an inert JSON block, for the Claude chat skill to read.

    ``<`` is escaped so that text inside news stories cannot close the tag.
    """
    if not pack:
        return ""
    data = json.dumps(pack, ensure_ascii=False, default=str).replace("<", "\\u003c")
    return f'<script type="application/json" id="{PACK_SCRIPT_ID}">{data}</script>'


def _froth_context(pack: dict | None) -> dict | None:
    """The run's froth snapshot from its briefing pack, else the latest score."""
    froth = ((pack or {}).get("macro") or {}).get("froth")
    return froth if froth and froth.get("composite_score") is not None else load_latest_context()


def _risk_detail(froth: dict | None, date_str: str) -> dict | None:
    """Full froth_score.json behind the run's froth snapshot, for the click-throughs."""
    as_of = (froth or {}).get("as_of") or date_str
    ctx = load_latest_context(as_of=as_of)
    if not ctx or not ctx.get("pillar_scores"):
        return None
    if froth and froth.get("composite_score") != ctx.get("composite_score"):
        # The detail must explain the numbers on the page, not another day's.
        logger.warning("froth detail skipped: file composite %s != report composite %s",
                       ctx.get("composite_score"), froth.get("composite_score"))
        return None
    return ctx


# ------------------------------------------------------------------
# Data model
# ------------------------------------------------------------------

@dataclass
class TickerRun:
    ticker: str
    group: str
    records: list[dict]
    failed: bool
    error_summary: str | None = None
    narrative_md: str | None = None

    def get_features(self) -> dict:
        for r in self.records:
            if r.get("type") == "feature_vector":
                return dict(r.get("features", {}))
        return {}

    def get_llm_decision(self) -> dict | None:
        for r in self.records:
            if r.get("type") == "llm_decision":
                return r.get("parsed") or r
        return None

    def get_final_trade(self) -> dict | None:
        for r in self.records:
            if r.get("type") == "final_trade":
                return r
        return None

    def get_execution(self) -> dict | None:
        for r in self.records:
            if r.get("type") == "execution":
                return r
        return None

    def get_data_snapshots(self) -> dict[str, dict]:
        snaps: dict[str, dict] = {}
        for r in self.records:
            if r.get("type") == "data_snapshot":
                label = r.get("label", "")
                snaps[label] = r.get("data", {})
        return snaps

    def get_composite(self) -> float | None:
        ft = self.get_final_trade()
        if ft:
            return ft.get("composite")
        return None

    def get_reasoning(self) -> list[str]:
        ft = self.get_final_trade()
        if ft:
            return ft.get("reasoning", [])
        return []

    def get_risk_overrides(self) -> list[str]:
        ft = self.get_final_trade()
        if ft:
            return ft.get("risk_overrides", [])
        return []


@dataclass
class RunData:
    date_str: str
    tickers: dict[str, TickerRun]
    previous: dict[str, TickerRun] | None = None  # previous run for deltas


# ------------------------------------------------------------------
# Audit file parsing
# ------------------------------------------------------------------

def _infer_ticker_from_label(label: str) -> str | None:
    """Extract ticker from a data_snapshot label like 'avgo_prices'."""
    for key in ALL_TICKER_KEYS:
        if label.lower().startswith(key.lower() + "_"):
            return key
        info = UNIVERSE[key]
        display = info.ticker_display.lower()
        if label.lower().startswith(display + "_"):
            return key
    return None


def _load_narrative(run_dir: Path, ticker: str) -> str | None:
    """Load a narrative markdown file for a ticker if it exists."""
    path = run_dir / f"narrative_{ticker}.md"
    if path.is_file():
        return path.read_text()
    return None


def parse_audit_dir(run_dir: Path) -> dict[str, TickerRun]:
    """Read all audit JSONL files in *run_dir*, return {ticker: TickerRun}.

    Tickers are identified by:
      1. data_snapshot label matching ``{ticker}_prices`` (preferred)
      2. Falling back to chronological order against UNIVERSE ordering
    """
    audit_files = sorted(run_dir.glob("audit_*.jsonl"))
    if not audit_files:
        return {}

    ticker_runs: dict[str, list[dict]] = {}
    ticker_from_label: dict[str, str] = {}

    # First pass: try to identify tickers from labels
    for f in audit_files:
        records = list(_read_jsonl(f))
        label_map: dict[str, str] = {}
        for r in records:
            if r.get("type") == "data_snapshot":
                label = r.get("label", "")
                inferred = _infer_ticker_from_label(label)
                if inferred:
                    label_map[label] = inferred

        if label_map:
            ticker = next(iter(label_map.values()))
            ticker_runs[ticker] = records
            ticker_from_label[f.name] = ticker

    # Second pass: fall back to ordering for unmatched files
    unmatched = [f for f in audit_files if f.name not in ticker_from_label]
    if unmatched:
        available = [k for k in ALL_TICKER_KEYS if k not in ticker_runs]
        for f, ticker in zip(unmatched, available):
            records = list(_read_jsonl(f))
            ticker_runs[ticker] = records

    result: dict[str, TickerRun] = {}
    for ticker, records in ticker_runs.items():
        info = UNIVERSE.get(ticker)
        group = info.group if info else "watchlist"
        result[ticker] = TickerRun(
            ticker=ticker,
            group="holding" if group == "holding" else "watchlist",
            records=records,
            failed=False,
            narrative_md=_load_narrative(run_dir, ticker),
        )

    return result


def _read_jsonl(path: Path) -> list[dict]:
    """Read a JSONL file and return list of parsed records."""
    records: list[dict] = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except (OSError, json.JSONDecodeError):
        pass
    return records


def find_previous_run_dir(run_dir: Path) -> Path | None:
    """Find the most recent earlier run directory."""
    parent = run_dir.parent
    current_name = run_dir.name
    candidates = sorted(
        d for d in parent.iterdir()
        if d.is_dir() and d.name.isdigit() and d.name < current_name
    )
    return candidates[-1] if candidates else None


def load_run(date_str: str) -> RunData:
    """Load run data for a given date string (YYYYMMDD or YYYY-MM-DD)."""
    clean = date_str.replace("-", "")
    run_dir = Path(f"data/outputs/mdu/{clean}")
    if not run_dir.is_dir():
        print(f"ERROR: Run directory not found: {run_dir}")
        sys.exit(1)

    tickers = parse_audit_dir(run_dir)

    # Detect failures: tickers in UNIVERSE without an audit file
    found_keys = set(tickers.keys())
    for key in ALL_TICKER_KEYS:
        if key not in found_keys:
            group = UNIVERSE[key].group
            tickers[key] = TickerRun(
                ticker=key,
                group="holding" if group == "holding" else "watchlist",
                records=[],
                failed=True,
                error_summary="No audit file found — batch run may have failed",
            )

    prev_run_dir = find_previous_run_dir(run_dir)
    previous: dict[str, TickerRun] | None = None
    if prev_run_dir:
        previous = parse_audit_dir(prev_run_dir)

    display_date = f"{clean[:4]}-{clean[4:6]}-{clean[6:8]}"
    return RunData(date_str=display_date, tickers=tickers, previous=previous)


# ------------------------------------------------------------------
# Analytics helpers
# ------------------------------------------------------------------

def macro_regime(tickers: dict[str, TickerRun]) -> str:
    """Extract the macro regime from the first available feature vector."""
    for t in ALL_TICKER_KEYS:
        tr = tickers.get(t)
        if tr and not tr.failed:
            f = tr.get_features()
            if f.get("macro_regime"):
                return str(f["macro_regime"])
    return "unknown"


def rate_trend(tickers: dict[str, TickerRun]) -> str:
    for t in ALL_TICKER_KEYS:
        tr = tickers.get(t)
        if tr and not tr.failed:
            f = tr.get_features()
            if f.get("rate_trend"):
                return str(f["rate_trend"])
    return "unknown"


def class_for_action(action: str) -> str:
    m = {"BUY": "action-buy", "SELL": "action-sell", "HOLD": "action-hold"}
    return m.get(action, "action-hold")


def action_sort_key(action: str) -> int:
    return {"BUY": 0, "SELL": 1, "HOLD": 2}.get(action, 3)


def sip(v: float) -> str:
    """Short integer percentage — no decimals for display."""
    return f"{v * 100:.0f}%"


def pct(v: float) -> str:
    return f"{v * 100:.1f}%"


def fmt_num(v: float) -> str:
    if abs(v) >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if abs(v) >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if abs(v) >= 1_000:
        return f"${v / 1_000:.1f}K"
    return f"${v:.2f}"


def generate_theme(tickers: dict[str, TickerRun]) -> str:
    """Deterministic template-based theme line — no LLM calls."""
    actions: dict[str, list[str]] = {"BUY": [], "SELL": [], "HOLD": []}
    dd_gated: list[str] = []
    near_buy: list[str] = []
    near_sell: list[str] = []
    failed_list: list[str] = []
    total_run = len([t for t in tickers.values() if not t.failed])

    for key, tr in tickers.items():
        if tr.failed:
            failed_list.append(key)
            continue
        ft = tr.get_final_trade()
        if ft:
            actions.setdefault(ft["action"], []).append(key)
        fv = tr.get_features()
        dd = fv.get("max_drawdown_6m", 0)
        if isinstance(dd, (int, float)) and dd < -0.15:
            dd_gated.append(key)
        conf = ft.get("confidence", 0) if ft else 0
        if 0.70 <= conf < 0.75:
            near_buy.append(key) if ft and ft.get("action") != "SELL" else None
        if conf <= 0.05:
            near_sell.append(key) if ft and ft.get("action") == "SELL" else None

    parts: list[str] = []
    n_hold = len(actions.get("HOLD", []))
    n_buy = len(actions.get("BUY", []))
    n_sell = len(actions.get("SELL", []))
    n_fail = len(failed_list)

    if n_buy + n_sell == 0:
        parts.append(f"All {n_hold} HOLD")
    elif n_buy > 0 and n_sell > 0:
        parts.append(f"{n_buy} BUY, {n_sell} SELL, {n_hold} HOLD")
    elif n_buy > 0:
        parts.append(f"{n_buy} BUY, {n_hold} HOLD")
    else:
        parts.append(f"{n_sell} SELL, {n_hold} HOLD")

    if dd_gated:
        parts.append(f"{len(dd_gated)} names drawdown-gated")
    if near_buy:
        parts.append(f"{len(near_buy)} names within 0.05 of BUY threshold")
    if n_fail:
        parts.append(f"{n_fail} FAILED")

    return "; ".join(parts)


def count_gate_firings(tickers: dict[str, TickerRun]) -> int:
    total = 0
    for tr in tickers.values():
        if tr.failed:
            continue
        total += len(tr.get_risk_overrides())
    return total


def compute_portfolio_value(tickers: dict[str, TickerRun]) -> float:
    """Sum of simulated execution values across tickers."""
    total = 0.0
    for tr in tickers.values():
        if tr.failed:
            continue
        ex = tr.get_execution()
        if ex:
            v = ex.get("simulated_value")
            if v is not None:
                total += float(v)
    return total


# ------------------------------------------------------------------
# HTML generation
# ------------------------------------------------------------------

# Font theme: IBM Plex Mono (as ReportMono) for numbers, tickers and tiles,
# from the lseg-earnings-analysis skill; text uses the system sans-serif
# stack, as in the weekly value screen. The mono file is regular weight
# only, so the browser synthesises bold.
FONT_DIR = Path(__file__).resolve().parent / "assets" / "fonts"
FONT_FACES = [
    ("ReportMono", "ibm-plex-mono.ttf", "400", "normal"),
]


def font_face_css() -> str:
    """Embed the theme fonts as data URIs so the page stays self-contained.

    A missing font file is logged and skipped; the CSS fallback stacks cover it.
    """
    rules = []
    for family, filename, weight, style in FONT_FACES:
        path = FONT_DIR / filename
        if not path.exists():
            logger.warning("Font missing, using fallback stack: %s", path)
            continue
        b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        rules.append(
            f'@font-face {{ font-family: "{family}"; '
            f'src: url("data:font/ttf;base64,{b64}") format("truetype"); '
            f"font-weight: {weight}; font-style: {style}; font-display: block; }}"
        )
    return "\n".join(rules)


CSS = r"""
:root { --font-mono: ReportMono, 'SF Mono', Menlo, Consolas, monospace; --font-body: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; --font-display: var(--font-body); }
* { margin: 0; padding: 0; box-sizing: border-box; }
body { background: #03102A; color: #C8D0DC; font-family: var(--font-body); font-size: 14px; line-height: 1.5; padding: 24px; max-width: 1200px; margin: 0 auto; }
h1, h2, h3 { color: #E8EDF5; font-weight: 500; }
h1, h2 { font-family: var(--font-display); font-weight: 500; }
h1 { font-size: 22px; margin-bottom: 4px; }
h2 { font-size: 16px; margin: 24px 0 12px; border-bottom: 1px solid #1A2F55; padding-bottom: 6px; }
h3 { font-size: 14px; margin: 16px 0 8px; color: #8FA3CC; }
.header-bar { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 20px; flex-wrap: wrap; gap: 8px; }
.header-meta { color: #6B86B0; font-size: 13px; }
.header-meta span { margin-right: 16px; }
.headline-strip { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 20px; }
.stat-card { background: #081D3F; border-radius: 8px; padding: 10px 16px; min-width: 90px; flex: 1; }
.stat-card .num { font-family: var(--font-mono); font-size: 20px; font-weight: 600; white-space: nowrap; }
.stat-card .num.green { color: #34C759; }
.stat-card .num.red { color: #FF453A; }
.stat-card .num.amber { color: #FFB547; }
.stat-card .num.cyan { color: #4FD8EB; }
.stat-card .stat-label { font-family: var(--font-body); font-size: 11px; color: #6B86B0; text-transform: uppercase; letter-spacing: 0.5px; margin-top: 2px; }
.stat-card .stat-sub { font-size: 11px; color: #4A6A94; margin-top: 1px; }
.theme-line { background: #081D3F; border-radius: 8px; padding: 10px 16px; flex: 1; min-width: 200px; display: flex; align-items: center; color: #8FA3CC; font-size: 13px; }
table { width: 100%; border-collapse: collapse; margin-bottom: 20px; }
th { text-align: left; font-family: var(--font-body); font-size: 11px; font-weight: 500; color: #4A6A94; text-transform: uppercase; letter-spacing: 0.5px; padding: 8px 10px; border-bottom: 1px solid #1A2F55; }
td { padding: 8px 10px; border-bottom: 1px solid #0E1E3D; font-size: 13px; font-family: var(--font-body); }
td.numeric { text-align: right; }
td.numeric, td.ticker, .mono, .action-buy, .action-sell, .action-hold { font-family: var(--font-mono); }
.brief { margin: 0 0 24px 0; }
.brief .stack { margin-bottom: 16px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; }
tr.failed td { color: #FF453A; }
tr.failed td.ticker { font-weight: 600; }
tr:hover td { background: rgba(46, 107, 255, 0.08); }
td.ticker { font-weight: 500; color: #E8EDF5; }
.ticker-group { font-size: 10px; color: #4A6A94; text-transform: uppercase; }
.action-buy { color: #34C759; font-weight: 600; }
.action-sell { color: #FF453A; font-weight: 600; }
.action-hold { color: #FFB547; font-weight: 600; }
.conf-bar { display: inline-block; height: 6px; border-radius: 3px; background: #1A2F55; position: relative; width: 60px; vertical-align: middle; margin-right: 6px; }
.conf-bar-fill { display: block; height: 6px; border-radius: 3px; background: #2E6BFF; }
.gate { display: inline-block; background: rgba(255, 181, 71, 0.15); color: #FFB547; font-size: 11px; padding: 1px 6px; border-radius: 3px; margin: 1px 2px; white-space: nowrap; }
.change-up { color: #34C759; }
.change-down { color: #FF453A; }
.change-none { color: #4A6A94; }
.drilldown { display: none; padding: 12px 16px 16px; background: #081D3F; border-radius: 0 0 8px 8px; margin-top: -1px; margin-bottom: 8px; }
.drilldown.open { display: block; }
.drilldown-grid { display: grid; grid-template-columns: auto 1fr; gap: 4px 16px; font-size: 13px; }
.drilldown-grid .dd-label { color: #4A6A94; font-family: var(--font-mono); font-size: 12px; }
.drilldown-grid .dd-value { font-family: var(--font-mono); }
.drilldown-grid .dd-value span { background: rgba(46, 107, 255, 0.1); color: #4FD8EB; padding: 0 4px; border-radius: 2px; }
.reasoning-list { list-style: none; padding: 0; }
.reasoning-list li { padding: 3px 0 3px 14px; position: relative; color: #C8D0DC; font-size: 13px; }
.reasoning-list li::before { content: '\2022'; position: absolute; left: 0; color: #2E6BFF; }
.delta-grid { display: grid; grid-template-columns: auto 1fr 1fr; gap: 4px 16px; font-size: 13px; }
.delta-grid .dd-label { color: #4A6A94; }
.delta-val { font-family: var(--font-mono); }
.expando { cursor: pointer; user-select: none; }
.expando:hover { opacity: 0.8; }
.expando-icon { color: #4A6A94; margin-right: 6px; font-size: 11px; }
.sort-by { cursor: pointer; }
.footer { text-align: center; color: #4A6A94; font-size: 11px; margin-top: 32px; padding-top: 16px; border-top: 1px solid #0E1E3D; }
.narrative-section { margin-top: 12px; border-top: 1px solid #1A2F55; padding-top: 8px; }
.narrative-header { font-size: 13px; color: #4FD8EB; padding: 4px 0; }
.narrative-body { font-family: var(--font-body); font-size: 13px; line-height: 1.6; color: #C8D0DC; padding: 8px 0; }
.narrative-body h3 { font-size: 15px; color: #E8EDF5; margin: 16px 0 8px; }
.narrative-body h4 { font-size: 14px; color: #8FA3CC; margin: 12px 0 6px; }
.narrative-body h5 { font-size: 13px; color: #6B86B0; margin: 10px 0 4px; }
.narrative-body ul { margin: 4px 0 8px 16px; padding: 0; }
.narrative-body li { margin: 2px 0; color: #C8D0DC; }
.narrative-body p { margin: 6px 0; }
.narrative-body hr { border: none; border-top: 1px solid #1A2F55; margin: 12px 0; }
.narrative-body strong { color: #E8EDF5; }
.footer a { color: #2E6BFF; text-decoration: none; }
@media (max-width: 800px) { body { padding: 12px; } .headline-strip { flex-direction: column; } .grid-2 { grid-template-columns: 1fr; } th, td { padding: 6px 8px; font-size: 12px; } }
"""


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _conf_bar(conf: float, width: int = 60) -> str:
    pct = max(0, min(100, conf * 100))
    return f'<span class="conf-bar" style="width:{width}px"><span class="conf-bar-fill" style="width:{pct}%"></span></span>'


def _gate_badges(overrides: list[str]) -> str:
    if not overrides:
        return '<span style="color:#4A6A94;font-size:11px">none</span>'
    parts = []
    for g in overrides:
        label = g[:40]
        parts.append(f'<span class="gate">{_escape(label)}</span>')
    return " ".join(parts)


def _action_cell(action: str, conf: float) -> str:
    cls = class_for_action(action)
    bar = _conf_bar(conf)
    return f'<span class="{cls}">{action}</span> {bar}<span class="mono">{conf:.2f}</span>'


def _delta_cell(prev: str | None, curr: str | None) -> str:
    """Simple action delta."""
    if prev is None or curr is None:
        return '<span class="change-none">—</span>'
    if prev == curr:
        return f'<span class="change-none">{_escape(curr)}</span>'
    arrow = "&#8593;" if curr == "BUY" else "&#8595;"
    cls = "change-up" if curr == "BUY" else "change-down"
    return f'<span class="{cls}">{arrow} {_escape(curr)}</span>'


def _conf_delta(prev: float | None, curr: float | None) -> str:
    if prev is None or curr is None:
        return ""
    diff = curr - prev
    if abs(diff) < 0.01:
        return '<span class="change-none">&#8776;</span>'
    cls = "change-up" if diff > 0 else "change-down"
    return f'<span class="{cls}">{diff:+.2f}</span>'


def _md_to_html(md: str) -> str:
    """Minimal markdown-to-HTML for research narratives.
    Supports: h1/h2, bold, lists, horizontal rules, paragraphs.
    """
    html_lines: list[str] = []
    in_list = False
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h3>{_escape(stripped[2:])}</h3>")
        elif stripped.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h4>{_escape(stripped[3:])}</h4>")
        elif stripped.startswith("### "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h5>{_escape(stripped[4:])}</h5>")
        elif stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            content = _escape(stripped[2:])
            content = content.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
            html_lines.append(f"<li>{content}</li>")
        elif stripped == "---":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("<hr>")
        elif stripped == "":
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append("")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            escaped = _escape(stripped)
            escaped = escaped.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
            html_lines.append(f"<p>{escaped}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _drilldown_html(ticker: str, tr: TickerRun) -> str:
    if tr.failed:
        error = _escape(tr.error_summary or "unknown error")
        return f'<div class="drilldown" id="dd-{ticker}"><p style="color:#FF453A">{error}</p></div>'

    ft = tr.get_final_trade()
    ld = tr.get_llm_decision()
    fv = tr.get_features()
    ex = tr.get_execution()
    snaps = tr.get_data_snapshots()

    lines: list[str] = ['<div class="drilldown" id="dd-{ticker}"><div class="drilldown-grid">'.format(ticker=ticker)]

    # Feature vector
    if fv:
        lines.append('<span class="dd-label">trend 3m / 12m</span>')
        val = fv.get("trend_3m", "—") if isinstance(fv.get("trend_3m"), (int, float)) else "—"
        val2 = fv.get("trend_12m", "—") if isinstance(fv.get("trend_12m"), (int, float)) else "—"
        lines.append(f'<span class="dd-value"><span>{val:+.1%}</span> / <span>{val2:+.1%}</span></span>')

        lines.append('<span class="dd-label">volatility 30d / 12m</span>')
        vol30 = fv.get("volatility_30d", "—")
        vol12 = fv.get("volatility_12m", "—")
        lines.append(f'<span class="dd-value"><span>{vol30}</span> / <span>{vol12}</span></span>')

        lines.append('<span class="dd-label">max drawdown 6m</span>')
        dd = fv.get("max_drawdown_6m", "—")
        if isinstance(dd, (int, float)):
            lines.append(f'<span class="dd-value"><span>{dd:+.1%}</span></span>')
        else:
            lines.append(f'<span class="dd-value">{_escape(str(dd))}</span>')

        lines.append('<span class="dd-label">macro regime</span>')
        lines.append(f'<span class="dd-value"><span>{_escape(str(fv.get("macro_regime", "—")))}</span></span>')

        lines.append('<span class="dd-label">rate / inflation</span>')
        lines.append(
            f'<span class="dd-value"><span>{_escape(str(fv.get("rate_trend", "—")))}</span>'
            f' / <span>{_escape(str(fv.get("inflation_trend", "—")))}</span></span>'
        )

        shock = fv.get("news_shock_flag", False)
        lines.append('<span class="dd-label">news shock</span>')
        lines.append(f'<span class="dd-value"><span style="color:#{"FF453A" if shock else "4A6A94"}">{shock}</span></span>')

    # Data sources
    if snaps:
        lines.append('<span class="dd-label" style="margin-top:8px">data sources</span>')
        lines.append('<span class="dd-value" style="margin-top:8px">')
        src_parts = []
        for lab, d in snaps.items():
            short = lab.replace("_prices", "").replace("_", " ").strip()[:20]
            if isinstance(d, dict):
                summary = "; ".join(f"{k}={v}" for k, v in d.items() if not isinstance(v, (list, dict)))
            elif isinstance(d, list):  # e.g. news_top
                summary = f"{len(d)} items"
            else:
                summary = str(d)[:80]
            src_parts.append(f'<span title="{_escape(lab)}: {_escape(summary)}">{_escape(short)}</span>')
        lines.append(" ".join(src_parts) + "</span>")

    # Reasoning
    reasoning = tr.get_reasoning()
    if reasoning:
        lines.append('<span class="dd-label" style="margin-top:8px">reasoning</span>')
        lis = "".join(f"<li>{_escape(r)}</li>" for r in reasoning)
        lines.append(f'<span class="dd-value" style="margin-top:8px"><ul class="reasoning-list">{lis}</ul></span>')

    # Override details
    overrides = tr.get_risk_overrides()
    if overrides:
        lines.append('<span class="dd-label">overrides</span>')
        lines.append(f'<span class="dd-value">{_gate_badges(overrides)}</span>')

    # Execution
    if ex:
        lines.append('<span class="dd-label">execution</span>')
        msg = ex.get("message", "")
        val = ex.get("simulated_value")
        val_str = fmt_num(val) if val else "—"
        lines.append(f'<span class="dd-value">{_escape(msg)} | port: {val_str}</span>')

    # LLM raw (expandable)
    if ld:
        lines.append('<span class="dd-label" style="margin-top:8px">LLM risk flags</span>')
        flags = ld.get("risk_flags", [])
        if flags:
            lines.append(f'<span class="dd-value" style="margin-top:8px">{_escape(", ".join(flags))}</span>')
        else:
            lines.append('<span class="dd-value" style="margin-top:8px;color:#4A6A94">none</span>')

    # Research narrative (skill-file research note)
    if tr.narrative_md:
        lines.append('</div>')  # close drilldown-grid
        lines.append('<div class="narrative-section">')
        lines.append('<div class="narrative-header expando" onclick="toggleNarrative(this)">')
        lines.append('<span class="expando-icon">&#9654;</span> Research Note')
        lines.append('</div>')
        lines.append('<div class="narrative-body" style="display:none">')
        lines.append(_md_to_html(tr.narrative_md))
        lines.append('</div>')
        lines.append('</div>')

    lines.append("</div>")
    return "\n".join(lines)


def _table_row(key: str, tr: TickerRun, prev: TickerRun | None, idx: int) -> str:
    if tr.failed:
        return (
            f'<tr class="failed">'
            f'<td class="ticker expando" onclick="toggle({idx})" data-target="dd-{key}">'
            f'<span class="expando-icon">&#9654;</span>{_escape(key)} '
            f'<span class="ticker-group">{tr.group}</span>'
            f'</td>'
            f'<td colspan="4">FAILED — {_escape(tr.error_summary or "no audit file")}</td>'
            f'<td></td>'
            f'</tr>'
            f'<tr><td colspan="6">'
            f'<div class="drilldown" id="dd-{key}"><p style="color:#FF453A">{_escape(tr.error_summary or "unknown error")}</p></div>'
            f'</td></tr>'
        )

    ft = tr.get_final_trade()
    action = ft["action"] if ft else "HOLD"
    conf = ft.get("confidence", 0.0) if ft else 0.0
    alloc = ft.get("allocation", 0.0) if ft else 0.0
    overrides = tr.get_risk_overrides()

    action_cell = _action_cell(action, conf)
    alloc_str = sip(alloc) if alloc > 0 else "0%"

    prev_action: str | None = None
    prev_conf: float | None = None
    if prev and not prev.failed:
        pft = prev.get_final_trade()
        if pft:
            prev_action = pft.get("action")
            prev_conf = pft.get("confidence")

    action_delta = _delta_cell(prev_action, action)
    conf_delta = _conf_delta(prev_conf, conf)

    gates = _gate_badges(overrides)

    row = (
        f'<tr>'
        f'<td class="ticker expando" onclick="toggle({idx})" data-target="dd-{key}">'
        f'<span class="expando-icon">&#9654;</span>{_escape(key)} '
        f'<span class="ticker-group">{tr.group}</span>'
        f'</td>'
        f'<td>{action_cell}</td>'
        f'<td class="numeric">{alloc_str}</td>'
        f'<td class="numeric">{gates}</td>'
        f'<td class="numeric">{action_delta}</td>'
        f'<td class="numeric">{conf_delta}</td>'
        f'</tr>'
    )

    # Next row: drilldown
    row += f'<tr><td colspan="6">{_drilldown_html(key, tr)}</td></tr>'

    return row


def gen_html(run: RunData) -> str:
    tickers = run.tickers
    date_str = run.date_str
    run_day = _short_date(dt.date.fromisoformat(date_str))
    previous = run.previous

    # Sort tickers by composite descending, failures last
    def sort_key(item: tuple[str, TickerRun]) -> tuple:
        key, tr = item
        if tr.failed:
            return (1, 0, 0, key)
        ft = tr.get_final_trade()
        conf = ft.get("confidence", 0) if ft else 0
        action_order = {"BUY": 0, "SELL": 1, "HOLD": 2}.get(ft.get("action", "HOLD") if ft else "HOLD", 3)
        return (0, action_order, -conf, key)

    sorted_items = sorted(tickers.items(), key=sort_key)

    n_total = len(ALL_TICKER_KEYS)
    n_success = sum(1 for t in tickers.values() if not t.failed)
    n_fail = n_total - n_success
    n_buy = sum(1 for t in tickers.values() if not t.failed and (f := t.get_final_trade()) and f["action"] == "BUY")
    n_sell = sum(1 for t in tickers.values() if not t.failed and (f := t.get_final_trade()) and f["action"] == "SELL")
    n_hold = sum(1 for t in tickers.values() if not t.failed and (f := t.get_final_trade()) and f["action"] == "HOLD")
    n_gate = count_gate_firings(tickers)
    regime = macro_regime(tickers)
    theme = generate_theme(tickers)
    port_val = compute_portfolio_value(tickers)

    rows = "\n".join(_table_row(k, tr, previous.get(k) if previous else None, i) for i, (k, tr) in enumerate(sorted_items))

    # Previous run comparison table (only if prev exists)
    prev_table = ""
    if previous:
        prev_rows: list[str] = []
        for key in ALL_TICKER_KEYS:
            tr = tickers.get(key)
            pr = previous.get(key)
            if not tr or tr.failed:
                continue
            if not pr or pr.failed:
                continue
            ft = tr.get_final_trade()
            pf = pr.get_final_trade()
            if not ft or not pf:
                continue
            action = ft["action"]
            conf = ft.get("confidence", 0)
            p_action = pf["action"]
            p_conf = pf.get("confidence", 0)
            action_d = _delta_cell(p_action, action)
            conf_d = _conf_delta(p_conf, conf)
            prev_rows.append(
                f'<tr><td class="ticker">{_escape(key)}</td>'
                f'<td>{_action_cell(p_action, p_conf)}</td>'
                f'<td>{_action_cell(action, conf)}</td>'
                f'<td>{action_d}</td>'
                f'<td class="numeric">{conf_d}</td></tr>'
            )
        if prev_rows:
            prev_date = previous[ALL_TICKER_KEYS[0]].records[0].get("timestamp", "")[:10] if previous[ALL_TICKER_KEYS[0]].records else "—"
            prev_table = f"""
<h2>Day-over-Day Delta</h2>
<table>
  <thead><tr>
    <th>Ticker</th><th>Previous ({_escape(prev_date[:10])})</th><th>Current ({_escape(date_str)})</th><th>Action Δ</th><th class="numeric">Conf Δ</th>
  </tr></thead>
  <tbody>
{chr(10).join(prev_rows)}
  </tbody>
</table>
"""

    mode_str = "PAPER"
    warnings: list[str] = []
    if n_fail > 0:
        warnings.append(f"{n_fail} ticker(s) FAILED — see red rows below")
    if regime == "risk_off":
        warnings.append("Risk-off macro regime caps new allocation to 30%")
    if n_gate > 20:
        warnings.append(f"{n_gate} gate firings — elevated risk environment")

    warning_block = ""
    if warnings:
        wlis = "".join(f"<li>{_escape(w)}</li>" for w in warnings)
        warning_block = f'<div style="background:rgba(255,69,58,0.1);border:1px solid rgba(255,69,58,0.3);border-radius:8px;padding:10px 16px;margin-bottom:20px;color:#FF453A;font-size:13px"><ul style="margin:0;padding-left:16px">{wlis}</ul></div>'

    sections, pack = _briefing(date_str)
    froth = _froth_context(pack)
    risk = _risk_detail(froth, date_str)
    pillar_details = ({p: pillar_detail_html(risk, p) for p in PILLAR_WEIGHTS}
                      if risk else None)
    charts = load_charts(BRIEFING_ROOT / date_str.replace("-", ""))
    briefing_block = (briefing_page_html(sections, pack, pillar_details, charts)
                      if sections else "")
    froth_click = (f'class="stat-card detail-click" '
                   f'{click_attrs("risk-froth", "Show how the froth score is built")}'
                   if risk else 'class="stat-card"')
    regime_click = (f'class="stat-card detail-click" '
                    f'{click_attrs("risk-regime", "Show the regime rules and readings")}'
                    if risk else 'class="stat-card"')
    risk_panels = (f'<div class="detail-panel wide" id="risk-froth">{froth_detail_html(risk)}</div>'
                   f'<div class="detail-panel wide" id="risk-regime">{regime_detail_html(risk)}</div>'
                   if risk else "")
    froth_score = froth.get("composite_score") if froth else None
    froth_num = f"{froth_score:.0f}" if froth_score is not None else "n/a"
    froth_band = _escape(str((froth or {}).get("band") or ""))

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{REPORT_TITLE} · {run_day}</title>
<style>
{font_face_css()}
{CSS}{CLICK_CSS}</style>
</head>
<body>

<div class="header-bar">
  <div>
    <h1>{REPORT_TITLE}</h1>
    <div class="header-meta">
      <span>{run_day}</span>
      <span>mode: {mode_str}</span>
      <span>{n_success}/{n_total} tickers</span>
    </div>
  </div>
</div>

{warning_block}

<div class="headline-strip">
  <div class="stat-card">
    <div class="num {'cyan' if n_buy > 0 else ''}">{n_buy}</div>
    <div class="stat-label">BUY</div>
  </div>
  <div class="stat-card">
    <div class="num {'amber' if n_hold > 0 else ''}">{n_hold}</div>
    <div class="stat-label">HOLD</div>
  </div>
  <div class="stat-card">
    <div class="num {'red' if n_sell > 0 else ''}">{n_sell}</div>
    <div class="stat-label">SELL</div>
  </div>
  <div class="stat-card">
    <div class="num {'red' if n_fail > 0 else 'amber'}">{n_fail}</div>
    <div class="stat-label">FAILED</div>
    <div class="stat-sub">of {n_total}</div>
  </div>
  <div class="stat-card">
    <div class="num">{n_gate}</div>
    <div class="stat-label">GATE FIRINGS</div>
    <div class="stat-sub">{len(ALL_TICKER_KEYS)} tickers</div>
  </div>
  <div class="stat-card">
    <div class="num cyan">{fmt_num(port_val)}</div>
    <div class="stat-label">PORTFOLIO VALUE</div>
    <div class="stat-sub">simulated</div>
  </div>
  <div {froth_click}>
    <div class="num" style="color:{house.froth_color(froth_score)}">{froth_num}</div>
    <div class="stat-label">FROTH</div>
    <div class="stat-sub">{froth_band}</div>
  </div>
  <div {regime_click}>
    <div class="num cyan">{_escape(regime.replace("_", " "))}</div>
    <div class="stat-label">MACRO REGIME</div>
  </div>
  <div class="theme-line">{_escape(theme)}</div>
</div>
{risk_panels}

{briefing_block}

<h2>Decisions</h2>
<table>
  <thead><tr>
    <th>Ticker</th><th>Action / Conf</th><th class="numeric">Alloc</th><th class="numeric">Gates</th><th class="numeric">Δ Action</th><th class="numeric">Δ Conf</th>
  </tr></thead>
  <tbody>
{rows}
  </tbody>
</table>

{prev_table}

<div class="footer">
  Generated {_send_stamp()} &mdash;
  <a href="latest.html">latest</a>
</div>

<script>
{CLICK_JS}
function toggle(idx) {{
  var target = document.getElementById('dd-' + document.querySelectorAll('[data-target]')[idx].getAttribute('data-target').replace('dd-', ''));
  var icon = document.querySelectorAll('.expando-icon')[idx];
  if (target) {{
    target.classList.toggle('open');
    icon.innerHTML = target.classList.contains('open') ? '&#9660;' : '&#9654;';
  }}
}}
function toggleNarrative(el) {{
  var body = el.parentElement.querySelector('.narrative-body');
  var icon = el.querySelector('.expando-icon');
  if (body) {{
    var isOpen = body.style.display !== 'none';
    body.style.display = isOpen ? 'none' : 'block';
    icon.innerHTML = isOpen ? '&#9654;' : '&#9660;';
  }}
}}
</script>
{_pack_block(pack)}
</body>
</html>"""

    return html


def gen_email_html(run: RunData) -> str:
    """Email-safe summary of *run*: house style, inline styles only, no JS.

    Built from lseg_quant.reporting.theme so it matches the attached report
    and the weekly value screen. The full interactive report (drilldowns,
    day-over-day deltas) is attached separately by workflows/mdu_email.py.
    """
    tickers = run.tickers
    date_str = run.date_str
    sections, pack = _briefing(date_str)

    n_total = len(ALL_TICKER_KEYS)
    n_success = sum(1 for t in tickers.values() if not t.failed)
    n_fail = n_total - n_success
    actions = [f["action"] for t in tickers.values() if not t.failed and (f := t.get_final_trade())]
    n_buy, n_hold, n_sell = actions.count("BUY"), actions.count("HOLD"), actions.count("SELL")
    regime = macro_regime(tickers)
    froth = _froth_context(pack)
    froth_score = froth.get("composite_score") if froth else None

    header = (
        f'<div style="font-family:{house.SANS};font-size:22px;font-weight:500;'
        f'color:{house.HEADING}">{house.esc(REPORT_TITLE)}</div>'
        f'<div style="font-family:{house.SANS};font-size:13px;color:{house.MUTED};margin-top:2px">'
        f'{_short_date(dt.date.fromisoformat(date_str))} &middot; paper mode &middot; '
        f'{n_success}/{n_total} tickers &middot; File sent {_send_stamp()}</div>')
    rows = [house.email_row(header, top=0)]
    if n_fail:
        rows.append(house.email_row(
            f'<div style="background:{house.tint(house.RED, 0.15, house.PAGE)};border-radius:8px;'
            f'padding:10px 14px;font-family:{house.SANS};font-size:13px;color:{house.RED}">'
            f'{n_fail} ticker(s) failed. See the red rows under Decisions.</div>'))
    tiles = [
        ("Buy", str(n_buy), house.GREEN if n_buy else house.MUTED, None),
        ("Hold", str(n_hold), house.AMBER if n_hold else house.MUTED, None),
        ("Sell", str(n_sell), house.RED if n_sell else house.MUTED, None),
        ("Failed", str(n_fail), house.RED if n_fail else house.MUTED, f"of {n_total}"),
        ("Froth", f"{froth_score:.0f}" if froth_score is not None else "n/a",
         house.froth_color(froth_score), (froth or {}).get("band")),
        ("Regime", regime.replace("_", " "), house.CYAN, None),
    ]
    rows.append(house.email_row(house.stat_tiles(tiles)))
    rows.append(house.email_row(
        f'<div style="font-family:{house.SANS};font-size:13px;color:{house.MUTED}">'
        f'{house.esc(generate_theme(tickers))}</div>', top=10))
    if sections:
        rows.append(briefing_email_rows(sections, pack))
    rows.append(house.email_row(house.section("Decisions", _email_decisions_table(tickers))))

    for key in ALL_TICKER_KEYS:
        tr = tickers.get(key)
        ft = tr.get_final_trade() if tr is not None and not tr.failed else None
        if not ft or ft.get("action") not in ("BUY", "SELL") or not tr.narrative_md:
            continue
        body = (f'<div style="font-family:{house.SANS};font-size:13px;line-height:1.6;'
                f'color:{house.TEXT}">{_md_to_html(tr.narrative_md)}</div>')
        rows.append(house.email_row(house.section(f"{key} research note ({ft['action']})", body)))

    rows.append(house.email_row(
        f'<div style="font-family:{house.SANS};font-size:12px;color:{house.MUTED}">'
        f'The full interactive report is attached as mdu-report-{house.esc(date_str)}.html. '
        'Open it in a browser for drilldowns and day-over-day changes.</div>', top=16))
    return house.email_document(f"{REPORT_TITLE} · {date_str}", "\n".join(rows))


def _email_decisions_table(tickers: dict[str, TickerRun]) -> str:
    """Decisions as an email-safe table: ticker, model action, confidence, gates."""
    cell = f"padding:7px 8px;border-bottom:1px solid {house.ROW_RULE};vertical-align:middle"
    head = (f"padding:6px 8px;border-bottom:1px solid {house.BORDER};font-family:{house.SANS};"
            f"font-size:11px;font-weight:500;color:{house.FAINT};text-transform:uppercase;"
            "letter-spacing:0.5px;text-align:left")
    trs: list[str] = []
    for key in ALL_TICKER_KEYS:
        tr = tickers.get(key)
        if tr is None:
            continue
        if tr.failed:
            trs.append(
                f'<tr><td style="{cell}">{house.mono(key, house.RED, weight=600)}</td>'
                f'<td colspan="3" style="{cell};font-family:{house.SANS};font-size:12px;'
                f'color:{house.RED}">Failed: {house.esc(tr.error_summary or "no data")}</td></tr>')
            continue
        ft = tr.get_final_trade() or {}
        action = ft.get("action", "HOLD")
        conf = float(ft.get("confidence") or 0.0)
        alloc = float(ft.get("allocation") or 0.0)
        model = house.mono(action, house.ACTION_COLORS.get(action, house.MUTED), size=12, weight=600)
        if alloc > 0:
            model += " " + house.mono(sip(alloc), house.TEXT, size=12)
        gates = ", ".join(g[:24] for g in tr.get_risk_overrides())
        trs.append(
            f'<tr><td style="{cell}">{house.mono(key, weight=600)}</td>'
            f'<td style="{cell}">{model}</td>'
            f'<td width="130" style="{cell}"><table role="presentation" width="100%" '
            f'cellpadding="0" cellspacing="0"><tr><td width="70">{house.bar(conf, house.BLUE, height=6)}</td>'
            f'<td style="padding-left:8px">{house.mono(f"{conf:.2f}", house.TEXT, size=12)}</td>'
            f'</tr></table></td>'
            f'<td style="{cell};font-family:{house.SANS};font-size:12px;color:{house.MUTED}">'
            f'{house.esc(gates)}</td></tr>')
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border-collapse:collapse">'
            f'<tr><th style="{head}">Ticker</th><th style="{head}">Model</th>'
            f'<th style="{head}">Confidence</th><th style="{head}">Risk gates</th></tr>'
            + "".join(trs) + "</table>")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main() -> int:
    import argparse

    p = argparse.ArgumentParser(description="Generate MDU daily HTML report")
    p.add_argument("--date", type=str, default=None,
                   help="Run date (YYYY-MM-DD or YYYYMMDD, default: latest)")
    p.add_argument("--out-dir", type=str, default="data/reports/Daily EQ Research",
                   help="Output directory (default: data/reports)")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    # Determine run date
    if args.date:
        date_raw = args.date.replace("-", "")
    else:
        run_dirs = sorted(
            d for d in Path("data/outputs/mdu").iterdir()
            if d.is_dir() and d.name.isdigit()
        )
        if not run_dirs:
            logger.error("No MDU run directories found in data/outputs/mdu/")
            return 1
        date_raw = run_dirs[-1].name

    display_date = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:8]}"

    # Load data
    run = load_run(date_raw)
    html = gen_html(run)

    # Write output
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / f"{display_date}.html"
    out_path.write_text(html)
    logger.info("Report written: %s", out_path)

    latest_path = out_dir / "latest.html"
    shutil.copy(str(out_path), str(latest_path))
    logger.info("Latest copy: %s", latest_path)

    print(f"\nReport: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
