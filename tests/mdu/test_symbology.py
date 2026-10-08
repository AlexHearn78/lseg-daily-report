"""Parsing of symbology_lookup results into universe.resolved.json rows."""
from __future__ import annotations

from lseg_quant.mdu.symbology import parse_lookup


def test_parse_lookup() -> None:
    payload = {"results": [
        {"_term": "NG.L", "_found": True, "RIC": "NG.L", "AssetState": "AC",
         "DocumentTitle": "National Grid PLC, Ordinary Share, London Stock Exchange",
         "IssuerOAPermID": "4295894468", "Currency": "GBp", "ExchangeName": "London Stock Exchange"},
        {"_term": "XX.L", "_found": False},
        {"_term": "OLD.L", "_found": True, "RIC": "OLD.L", "AssetState": "DC"},
        {"_term": "AB.L", "_found": True, "RIC": "ABC.L", "AssetState": "AC"},
    ]}
    out = parse_lookup(payload)
    assert out["NG.L"] == {"ric": "NG.L", "name": "National Grid PLC", "permid": 4295894468,
                           "currency": "GBp", "exchange": "London Stock Exchange"}
    assert out["XX.L"]["error"] == "not found"
    assert "not active" in out["OLD.L"]["error"]
    assert "different RIC" in out["AB.L"]["error"]
