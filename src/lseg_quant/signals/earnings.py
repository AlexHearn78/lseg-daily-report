"""Post-results earnings signal, in shadow mode.

For a company within 60 trading days of its latest results, combine

- estimate revisions since the last pre-results snapshot (40%),
- EPS and revenue surprise against consensus (25%),
- guidance change (25%) and change in management tone (10%), when the
  earnings-call analysis supplies them,

into a score from -1 to +1 (weights renormalise over what is available).
Then show what the composite would be if that score carried 20% of it,
fading linearly to zero at 60 trading days. Shadow mode: the rating the
model issues is unchanged; the report only shows the would-be result.

Surprise uses the last consensus snapshot taken before the announcement
("pre_results"). Until the snapshot history covers a results date, it
falls back to the latest published consensus and is labelled so.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from lseg_quant.mdu.scoring import composite_to_decision

WINDOW_TRADING_DAYS = 60
PEAK_WEIGHT = 0.20
COMPONENT_WEIGHTS = {"revisions": 0.40, "surprise": 0.25, "guidance": 0.25, "tone": 0.10}
# Percentage move that counts as a full +/-1 for each input.
FULL_SCALE = {"EPS": 10.0, "REV": 5.0, "revision": 5.0}
# Surprises beyond this are usually one-offs (disposal gains, mark-ups), so
# they count at half strength; revisions show whether analysts believe them.
OUTLIER_PCT = 50.0
OUTLIER_CAP = 0.5


def _clip(x: float) -> float:
    return max(-1.0, min(1.0, x))


def _action(composite: float) -> str:
    return composite_to_decision(composite)[0]


def trading_days_between(start: dt.date, end: dt.date) -> int:
    return int(np.busday_count(start, end))


def _matching(rows: list[dict], kind: str, period_type: str, period_end: str, measure: str) -> list[dict]:
    return [r for r in rows if r["kind"] == kind and r["period_type"] == period_type
            and r["period_end"] == period_end and r["measure"] == measure and r["value"] is not None]


def _before(rows: list[dict], period_type: str, period_end: str, measure: str,
            cutoff: dt.date) -> dict | None:
    """Latest estimate snapshot taken strictly before *cutoff*."""
    found = [r for r in _matching(rows, "estimate", period_type, period_end, measure)
             if r["snapshot_date"] < cutoff.isoformat()]
    return max(found, key=lambda r: r["snapshot_date"]) if found else None


def _latest(rows: list[dict], period_type: str, period_end: str, measure: str) -> dict | None:
    found = _matching(rows, "estimate", period_type, period_end, measure)
    return max(found, key=lambda r: r["snapshot_date"]) if found else None


def _pct(new: float, old: float) -> float:
    return (new - old) / abs(old) * 100.0


def earnings_signal(key: str, rows: list[dict], as_of: dt.date, composite: float | None,
                    guidance: int | None = None, tone: float | None = None) -> dict[str, Any] | None:
    """The signal for one ticker, or None outside the post-results window."""
    actuals = [r for r in rows if r["kind"] == "actual" and r.get("announce_utc")]
    if not actuals:
        return None
    latest = max(actuals, key=lambda r: r["announce_utc"])
    announce = dt.datetime.fromisoformat(latest["announce_utc"])
    period_end = latest["period_end"]
    days = trading_days_between(announce.date(), as_of)
    if not 0 <= days <= WINDOW_TRADING_DAYS:
        return None

    surprise: dict[str, dict] = {}
    for measure in ("EPS", "REV"):
        actual = next((r["value"] for r in actuals
                       if r["period_end"] == period_end and r["measure"] == measure), None)
        ref = _before(rows, "Quarter", period_end, measure, announce.date())
        vintage = "pre_results"
        if ref is None:
            ref, vintage = _latest(rows, "Quarter", period_end, measure), "latest_published"
        if actual is None or ref is None or not ref["value"]:
            continue
        surprise[measure] = {"actual": actual, "consensus": round(ref["value"], 4),
                             "pct": round(_pct(actual, ref["value"]), 2), "vintage": vintage,
                             "consensus_date": ref["snapshot_date"]}

    revisions: dict[str, dict] = {}
    year_ends = sorted({r["period_end"] for r in rows if r["kind"] == "estimate"
                        and r["period_type"] == "Year" and r["period_end"] > period_end})[:2]
    for label, year_end in zip(("FY1", "FY2"), year_ends):
        before = _before(rows, "Year", year_end, "EPS", announce.date())
        now = _latest(rows, "Year", year_end, "EPS")
        if before and now and before["value"]:
            revisions[label] = {"from": before["value"], "to": now["value"],
                                "pct": round(_pct(now["value"], before["value"]), 2),
                                "since": before["snapshot_date"]}

    notes: list[str] = []
    components: dict[str, float] = {}
    if surprise:
        parts = []
        for measure, s in surprise.items():
            part = _clip(s["pct"] / FULL_SCALE[measure])
            if abs(s["pct"]) > OUTLIER_PCT:
                part = max(-OUTLIER_CAP, min(OUTLIER_CAP, part))
                s["outlier"] = True
                notes.append(f"{measure} surprise of {s['pct']:+.0f}% is unusually large "
                             "(often a one-off) and counts at half strength.")
            parts.append(part)
        components["surprise"] = round(sum(parts) / len(parts), 3)
    if revisions:
        parts = [_clip(r["pct"] / FULL_SCALE["revision"]) for r in revisions.values()]
        components["revisions"] = round(sum(parts) / len(parts), 3)
    if guidance is not None:
        components["guidance"] = float(_clip(guidance))
    if tone is not None:
        components["tone"] = round(_clip(tone), 3)

    score = None
    if components:
        total = sum(COMPONENT_WEIGHTS[k] for k in components)
        score = round(sum(COMPONENT_WEIGHTS[k] * v for k, v in components.items()) / total, 3)
    weight = round(PEAK_WEIGHT * max(0.0, 1 - days / WINDOW_TRADING_DAYS), 3)

    shadow = None
    if composite is not None and score is not None:
        shadow = round(composite * (1 - weight) + weight * score, 3)

    if any(s["vintage"] == "latest_published" for s in surprise.values()):
        notes.append("Surprise uses the latest published consensus; no pre-results snapshot yet.")
    if not revisions:
        notes.append("Revisions need a pre-results consensus snapshot.")
    if guidance is None:
        notes.append("Guidance and tone arrive with the earnings-call analysis.")

    return {
        "key": key,
        "announce_utc": latest["announce_utc"],
        "period_end": period_end,
        "trading_days_since": days,
        "surprise": surprise,
        "revisions": revisions,
        "components": components,
        "score": score,
        "weight": weight,
        "composite": None if composite is None else round(composite, 3),
        "shadow_composite": shadow,
        "action": None if composite is None else _action(composite),
        "shadow_action": None if shadow is None else _action(shadow),
        "notes": notes,
    }
