#!/usr/bin/env python3
"""Replay the daily-briefing eval cases offline.

Runs the gate (scripts/check_sections.py) on each stored candidate in
cases.json against the fixture pack and checks the expected outcome.

Usage:
    python3 .claude/skills/daily-briefing/evals/run_evals.py
    python3 .claude/skills/daily-briefing/evals/run_evals.py --run <briefing_dir>

``--run`` also checks a real run's folder (pack.json, sections.json and
lookups.json) and prints its gate result, for grading with rubric.md.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))

import check_sections as gate  # noqa: E402


def codes(fails: list[str]) -> set[str]:
    return {f.split("]")[0].split("[")[1] for f in fails}


def main(argv: list[str]) -> int:
    spec = json.loads((HERE / "cases.json").read_text())
    pack = json.loads((HERE / spec["pack"]).read_text())
    lex = gate.load(gate.LEXICON)
    ss, ls = gate.load(gate.SECTIONS_SCHEMA), gate.load(gate.LOOKUPS_SCHEMA)
    failed = 0
    for case in spec["cases"]:
        sections = json.loads((HERE / case["candidate"]).read_text())
        lookups = json.loads((HERE / case["lookups"]).read_text()) if case.get("lookups") else None
        rep = gate.check(pack, sections, lookups, lex, ss, ls)
        got = codes(rep.fails)
        problems = []
        if case["expect"] == "pass" and rep.fails:
            problems.append(f"expected pass, got {sorted(got)}")
        if case["expect"] == "fail" and not rep.fails:
            problems.append("expected failures, got pass")
        for c in case.get("must_fail", []):
            if c not in got:
                problems.append(f"{c} not raised")
        for c in case.get("must_not_fail", []):
            if c in got:
                problems.append(f"{c} raised unexpectedly")
        status = "ok  " if not problems else "FAIL"
        failed += bool(problems)
        print(f"{status} {case['id']:<18} {sorted(got) or 'pass'}"
              + (f"  <- {'; '.join(problems)}" if problems else ""))
        if problems:
            for line in rep.fails:
                print("       " + line)
    print(f"\n{len(spec['cases']) - failed}/{len(spec['cases'])} cases as expected")

    if "--run" in argv:
        run = Path(argv[argv.index("--run") + 1])
        rep = gate.run_dir(run)
        print(f"\nReal run {run}:")
        for line in rep.fails + rep.warns:
            print("  " + line)
        print("  PASS" if rep.ok else f"  FAIL ({len(rep.fails)} failures)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
