"""Consensus per-share values must be in the quote currency."""
from __future__ import annotations

from lseg_quant.mdu.currency import align_consensus_currency


def _est(ccy: str, value: float = 4.21) -> dict:
    return {"date": "2026-12-31", "measures": {
        "epsPerShare": {"value": value, "high": value * 1.1, "normCurrDesc": ccy},
        "revMillion": {"value": 3000.0, "normCurrDesc": ccy},
    }}


def test_same_currency_unchanged() -> None:
    out, notes = align_consensus_currency([_est("USD")], "USD")
    assert out[0]["measures"]["epsPerShare"]["value"] == 4.21
    assert notes == []


def test_gbp_consensus_rescaled_to_pence() -> None:
    out, notes = align_consensus_currency([_est("GBP", 0.8973)], "GBp")
    eps = out[0]["measures"]["epsPerShare"]
    assert round(eps["value"], 2) == 89.73
    assert eps["normCurrDesc"] == "GBp"
    assert notes == []


def test_pence_consensus_with_pence_quote_unchanged() -> None:
    out, _ = align_consensus_currency([_est("GBp", 89.73)], "GBp")
    assert out[0]["measures"]["epsPerShare"]["value"] == 89.73


def test_other_currency_drops_per_share_only() -> None:
    # NKT.CO: price in DKK, EPS consensus in EUR
    out, notes = align_consensus_currency([_est("EUR")], "DKK")
    assert "epsPerShare" not in out[0]["measures"]
    assert "revMillion" in out[0]["measures"]
    assert len(notes) == 1 and "EUR" in notes[0]
