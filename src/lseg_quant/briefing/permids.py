"""Company names and organisation PermIDs for the transcripts tool.

Both come from ``config/universe.resolved.json`` via ``UNIVERSE``; run
``workflows/resolve_universe.py`` after changing the universe. A name with no
PermID is skipped by the earnings-call lookup.
"""
from __future__ import annotations

from lseg_quant.mdu.config import UNIVERSE


def company_name(key: str) -> str:
    info = UNIVERSE.get(key)
    return info.name if info and info.name else key


def transcript_permid(key: str) -> int | None:
    info = UNIVERSE.get(key)
    return info.permid if info else None
