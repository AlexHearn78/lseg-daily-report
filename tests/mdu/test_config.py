"""Universe and report config loading."""
from __future__ import annotations

from pathlib import Path

import pytest

from lseg_quant.mdu.config import UNIVERSE, load_report_config, load_universe

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "config"


def test_universe_merges_resolved_details() -> None:
    ng = UNIVERSE["NG"]
    assert ng.ric == "NG.L" and ng.ticker == "NG.L"
    assert ng.currency == "GBp"
    assert ng.permid == 4295894468
    assert ng.exchange == "XLON"
    assert list(UNIVERSE) == ["MSFT", "NVDA", "NG", "TSLA"]


def test_stale_resolved_entry_is_ignored() -> None:
    tsla = UNIVERSE["TSLA"]  # resolved file has a different RIC for TSLA
    assert tsla.permid is None
    assert tsla.name == "TSLA"
    assert tsla.group == "watchlist"


def test_duplicate_key_rejected(tmp_path: Path) -> None:
    (tmp_path / "universe.yaml").write_text(
        "names:\n  - {key: A, ric: A.N}\n  - {key: a, ric: B.N}\n")
    with pytest.raises(ValueError, match="Duplicate"):
        load_universe(tmp_path)


def test_report_config_defaults(tmp_path: Path) -> None:
    assert load_report_config(tmp_path)["title"] == "Daily Report"
    assert load_report_config(FIXTURES)["title"] == "Test Report"
