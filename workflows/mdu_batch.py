#!/usr/bin/env python3
"""Run the analyst for every name in config/universe.yaml.

Each name runs in its own subprocess so that one failure does not abort
the rest. Holdings run first, then the watchlist.

Usage:
    uv run workflows/mdu_batch.py [--date YYYY-MM-DD]
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from lseg_quant.mdu.config import UNIVERSE

logger = logging.getLogger("mdu_batch")

HOLDINGS = [k for k, v in UNIVERSE.items() if v.group == "holding"]
WATCHLIST = [k for k, v in UNIVERSE.items() if v.group == "watchlist"]

ALL_TICKERS = HOLDINGS + WATCHLIST


def run_ticker(
    ticker: str,
    date: str | None = None,
    timeout: int = 180,
) -> dict:
    """Run mdu_run.py for a single ticker in a subprocess.

    Returns a result dict with ticker, exit_code, duration, and error (if any).
    """
    start = time.time()
    cmd = [
        sys.executable, "-W", "ignore", "workflows/mdu_run.py",
        "--ticker", ticker,
    ]
    if date:
        cmd.extend(["--date", date])

    result: dict = {
        "ticker": ticker,
        "group": "holding" if ticker in HOLDINGS else "watchlist",
        "exit_code": -1,
        "duration_s": 0.0,
        "error": None,
    }

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        result["duration_s"] = round(time.time() - start, 1)
        result["exit_code"] = proc.returncode
        if proc.returncode != 0:
            stderr = proc.stderr.strip()[-500:] if proc.stderr else ""
            result["error"] = f"exit {proc.returncode}: {stderr}"
            logger.error("[%s] FAILED (exit=%d, %.1fs) %s",
                         ticker, proc.returncode, result["duration_s"],
                         result["error"])
        else:
            logger.info("[%s] OK (%.1fs)", ticker, result["duration_s"])
    except subprocess.TimeoutExpired:
        result["duration_s"] = round(time.time() - start, 1)
        result["exit_code"] = -9
        result["error"] = f"timed out after {timeout}s"
        logger.error("[%s] TIMEOUT (%ds)", ticker, timeout)
    except Exception as e:
        result["duration_s"] = round(time.time() - start, 1)
        result["exit_code"] = -1
        result["error"] = str(e)
        logger.exception("[%s] EXCEPTION: %s", ticker, e)

    return result


def run_universe(date: str | None = None) -> list[dict]:
    """Run all tickers sequentially with error isolation."""
    results: list[dict] = []
    total_start = time.time()

    for ticker in ALL_TICKERS:
        result = run_ticker(ticker, date=date)
        results.append(result)

    total_elapsed = time.time() - total_start
    successes = sum(1 for r in results if r["exit_code"] == 0)
    failures = sum(1 for r in results if r["exit_code"] != 0)

    logger.info("=" * 60)
    logger.info("BATCH COMPLETE: %d/%d OK, %d failed, %.1fs total",
                successes, len(results), failures, total_elapsed)
    for r in results:
        status = "OK" if r["exit_code"] == 0 else "FAIL"
        group = "H" if r["group"] == "holding" else "W"
        logger.info("  [%s] %s %s (%.1fs)%s",
                    status, group, r["ticker"], r["duration_s"],
                    f" \u2014 {r['error']}" if r["error"] else "")
    logger.info("=" * 60)

    return results


def run_report(date: str | None = None) -> None:
    """Generate the HTML report after batch completion."""
    import subprocess
    cmd = [sys.executable, "-W", "ignore", "workflows/mdu_report.py"]
    if date:
        cmd.extend(["--date", date])
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode == 0:
            last_line = [l for l in proc.stdout.strip().splitlines() if l.strip()][-1]
            logger.info("Report: %s", last_line)
        else:
            logger.warning("Report generation stderr: %s", proc.stderr.strip()[-300:])
    except Exception as e:
        logger.warning("Report generation failed: %s", e)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="Run the analyst across the universe")
    p.add_argument("--date", type=str, default=None,
                   help="Calculation date (YYYY-MM-DD)")
    p.add_argument("--no-report", action="store_true",
                   help="Skip HTML report generation")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stderr,
    )

    results = run_universe(date=args.date)
    n_fail = sum(1 for r in results if r["exit_code"] != 0)

    if not args.no_report:
        run_report(args.date)

    return 1 if n_fail > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
