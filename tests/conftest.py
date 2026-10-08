"""Point the config loader at the test fixture universe before anything imports it."""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ["REPORT_CONFIG_DIR"] = str(ROOT / "tests" / "fixtures" / "config")
sys.path.insert(0, str(ROOT / "src"))
