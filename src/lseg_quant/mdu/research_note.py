from __future__ import annotations

import logging
from dataclasses import dataclass, field

from lseg_quant.mdu.analyst import AnalystDecision
from lseg_quant.mdu.decision import FinalTrade
from lseg_quant.mdu.features import FeatureVector
from lseg_quant.mdu.research import ResearchContext

logger = logging.getLogger(__name__)


@dataclass
class ResearchNote:
    ticker: str
    as_of: str
    action: str
    confidence: float
    allocation: float
    narrative_markdown: str
    analyst_scores: dict[str, float] = field(default_factory=dict)
    risk_overrides: list[str] = field(default_factory=list)
    dimension_scores: dict[str, float] = field(default_factory=dict)
    composite: float = 0.0


def generate_narrative(
    ticker: str,
    as_of: str,
    context: ResearchContext,
    bp_decision: AnalystDecision,
    final_trade: FinalTrade,
    features: FeatureVector,
    dimension_scores: dict[str, float],
    composite: float,
) -> ResearchNote:
    """Build the research note for a BUY or SELL from the analyst scores.

    The note is deterministic: it restates the scores, reasoning and risk
    gates. Written commentary comes from the daily briefing step instead.
    """
    narrative = _template_fallback(ticker, bp_decision, final_trade, dimension_scores, composite)

    return ResearchNote(
        ticker=ticker,
        as_of=as_of,
        action=final_trade.action,
        confidence=final_trade.confidence,
        allocation=final_trade.allocation,
        narrative_markdown=narrative,
        analyst_scores={"composite": composite},
        risk_overrides=final_trade.risk_overrides,
        dimension_scores=dimension_scores,
        composite=composite,
    )


def _template_fallback(
    ticker: str,
    bp_decision: AnalystDecision,
    final_trade: FinalTrade,
    dimension_scores: dict[str, float],
    composite: float,
) -> str:
    """Concise narrative built from the analyst scores."""
    lines: list[str] = [
        f"# Equity Research Summary — {ticker}",
        "",
        "---",
        "",
        "## Investment Thesis",
        "",
        f"**Recommendation:** {final_trade.action}",
        f"**Confidence:** {final_trade.confidence:.0%}",
        f"**Allocation:** {final_trade.allocation:.0%} of portfolio",
        "",
    ]
    for r in bp_decision.reasoning:
        lines.append(f"- {r}")
    lines.append("")

    lines.append("### Dimension Scores")
    for dim, score in dimension_scores.items():
        lines.append(f"- **{dim.title()}:** {score:+.3f}")
    lines.append(f"- **Composite:** {composite:.3f}")
    lines.append("")

    overrides = getattr(final_trade, 'risk_overrides', None) or getattr(final_trade, 'overrides', None)
    if overrides:
        lines.append("### Risk Overrides Applied")
        for o in overrides:
            lines.append(f"- {o}")
        lines.append("")

    if bp_decision.risk_flags:
        lines.append("### Risk Flags")
        for f in bp_decision.risk_flags:
            lines.append(f"- {f}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "*Automated summary from the analyst scoring engine.*",
    ])

    return "\n".join(lines)
