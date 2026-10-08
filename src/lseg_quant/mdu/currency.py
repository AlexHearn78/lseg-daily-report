"""Keep IBES per-share consensus in the same currency as the quote.

IBES reports per-share estimates in the company's reporting currency, which
need not be the listing currency: NKT.CO trades in DKK but its EPS consensus
comes back in EUR. Dividing a DKK price by a EUR EPS overstates the P/E about
7.5 times. London prices are in pence (GBp) while consensus may be in GBP.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

PER_SHARE_MEASURES = ("epsPerShare", "ebitdaPerShare", "dividendPerShare", "fcfPerShare")

# Minor units LSEG uses for quotes: code -> (major currency, minor units per major)
_MINOR = {"GBp": ("GBP", 100.0), "GBX": ("GBP", 100.0), "ZAc": ("ZAR", 100.0), "ILA": ("ILS", 100.0)}


def _major(ccy: str) -> tuple[str, float]:
    """Return (major currency, minor units per major) for a currency code."""
    if ccy in _MINOR:
        return _MINOR[ccy]
    return ccy.upper(), 1.0


def align_consensus_currency(
    estimates: list[dict[str, Any]], quote_currency: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return estimates with per-share values in the quote currency, plus notes.

    Same major currency: values are rescaled (GBP -> GBp). A different
    currency: the per-share values are removed, so no ratio is computed
    from mismatched units. Estimates without a currency tag are left alone.
    """
    if not quote_currency:
        return estimates, []
    q_major, q_units = _major(quote_currency)
    notes: list[str] = []
    out: list[dict[str, Any]] = []
    for est in estimates:
        measures = dict(est.get("measures") or {})
        for name in PER_SHARE_MEASURES:
            m = measures.get(name)
            ccy = (m or {}).get("normCurrDesc")
            if not m or not ccy or m.get("value") is None:
                continue
            c_major, c_units = _major(ccy)
            if c_major != q_major:
                measures.pop(name)
                notes.append(f"{name} {est.get('date', '')[:10]} in {ccy}, price in {quote_currency}: dropped")
                continue
            factor = q_units / c_units
            if factor != 1.0:
                m = dict(m)
                for k in ("value", "high", "low", "standardDeviation"):
                    if isinstance(m.get(k), (int, float)):
                        m[k] = m[k] * factor
                m["normCurrDesc"] = quote_currency
                measures[name] = m
        out.append({**est, "measures": measures})
    for n in notes:
        logger.warning("Consensus currency mismatch: %s", n)
    return out, notes
