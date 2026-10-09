#!/usr/bin/env python3
"""Pull the briefing pack out of an attached daily report (Claude chat use).

The nightly report embeds its facts pack as
``<script type="application/json" id="briefing-pack">``. This writes it to
``<out_dir>/pack.json`` with an empty ``lookups.json`` beside it, so the gate
can run in a chat sandbox exactly as it does in the nightly job.

    python3 scripts/extract_pack.py daily-report-2026-10-08.html ./briefing
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PATTERN = re.compile(
    r'<script type="application/json" id="briefing-pack">(.*?)</script>', re.DOTALL)


def extract(html: str) -> dict:
    match = PATTERN.search(html)
    if not match:
        raise ValueError("no briefing pack in this file: attach the daily-report-<date>.html "
                         "from the email, not a screenshot or the email body")
    return json.loads(match.group(1))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    report, out_dir = Path(argv[0]), Path(argv[1])
    try:
        pack = extract(report.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}")
        return 1
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pack.json").write_text(json.dumps(pack, indent=2, ensure_ascii=False))
    lookups = out_dir / "lookups.json"
    if not lookups.exists():
        lookups.write_text("[]\n")
    stories = ", ".join(s.get("key", "?") for s in pack.get("top_stories", []))
    print(f"pack for {pack.get('date')} written to {out_dir}/pack.json; top stories: {stories or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
