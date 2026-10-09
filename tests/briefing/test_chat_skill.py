"""The report carries its pack for the Claude chat skill, and the chat skill packages."""
from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "workflows"))

from mdu_report import _pack_block  # noqa: E402


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


extract_pack = _load(ROOT / ".claude/skills/daily-briefing/scripts/extract_pack.py", "extract_pack")


def test_pack_round_trips_through_the_report(tmp_path: Path) -> None:
    pack = {"date": "2026-10-07", "top_stories": [
        {"key": "HLDN", "stories": [{"text": "a </script><b>tag</b> & “quotes”"}]}]}
    html = f"<html><body><p>report</p>{_pack_block(pack)}</body></html>"
    assert "</script><b>" not in html          # story text cannot close the tag
    assert extract_pack.extract(html) == pack
    report = tmp_path / "daily-report-2026-10-07.html"
    report.write_text(html, encoding="utf-8")
    assert extract_pack.main([str(report), str(tmp_path / "b")]) == 0
    assert json.loads((tmp_path / "b" / "pack.json").read_text()) == pack
    assert json.loads((tmp_path / "b" / "lookups.json").read_text()) == []


def test_no_pack_is_a_clear_failure(tmp_path: Path) -> None:
    assert _pack_block(None) == ""
    report = tmp_path / "email.html"
    report.write_text("<html>no pack</html>")
    assert extract_pack.main([str(report), str(tmp_path / "b")]) == 1


def test_chat_skill_zip(tmp_path: Path, monkeypatch) -> None:
    build = _load(ROOT / "scripts/build_chat_skill.py", "build_chat_skill")
    monkeypatch.setattr(build, "OUT", tmp_path / "daily-briefing.zip")
    assert build.main() == 0
    names = zipfile.ZipFile(tmp_path / "daily-briefing.zip").namelist()
    assert "daily-briefing/SKILL.md" in names
    assert "daily-briefing/scripts/check_sections.py" in names
    assert "daily-briefing/scripts/extract_pack.py" in names
    assert not any("/evals/" in n for n in names)
