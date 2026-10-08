"""Header date and London timestamp formats in the daily report."""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "workflows"))

from mdu_report import _send_stamp, _short_date  # noqa: E402

UTC = dt.timezone.utc


def test_short_date() -> None:
    assert _short_date(dt.date(2026, 9, 11)) == "11 Sep 26"
    assert _short_date(dt.date(2026, 9, 5)) == "5 Sep 26"


def test_send_stamp_follows_bst_and_gmt() -> None:
    summer = dt.datetime(2026, 9, 13, 12, 48, tzinfo=UTC)   # BST, UTC+1
    winter = dt.datetime(2026, 12, 1, 9, 5, tzinfo=UTC)     # GMT, UTC+0
    assert _send_stamp(summer) == "13 Sep 26 – 13:48 (London)"
    assert _send_stamp(winter) == "1 Dec 26 – 09:05 (London)"
