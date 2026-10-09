#!/usr/bin/env python3
"""Package the daily-briefing skill for upload to Claude chat.

Takes the shared instructions, references, examples and scripts from
.claude/skills/daily-briefing (the version the nightly job runs), adds the
chat entry point chat-skill/SKILL.md, and writes dist/daily-briefing.zip.
Upload it in Claude under Settings > Capabilities > Skills.

    uv run scripts/build_chat_skill.py
"""
from __future__ import annotations

import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / ".claude" / "skills" / "daily-briefing"
CHAT_SKILL_MD = ROOT / "chat-skill" / "SKILL.md"
OUT = ROOT / "dist" / "daily-briefing.zip"
FOLDERS = ("instructions", "references", "good-vs-bad", "scripts")


def files() -> list[tuple[Path, str]]:
    """(source, path inside the zip) for every file in the chat skill."""
    out = [(CHAT_SKILL_MD, "daily-briefing/SKILL.md")]
    for folder in FOLDERS:
        for path in sorted((SHARED / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                out.append((path, f"daily-briefing/{path.relative_to(SHARED)}"))
    return out


def main() -> int:
    OUT.parent.mkdir(exist_ok=True)
    entries = files()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for src, arc in entries:
            zf.write(src, arc)
    print(f"wrote {OUT} ({len(entries)} files)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
