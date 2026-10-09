#!/usr/bin/env python3
"""Gate for the daily-briefing skill: checks sections.json against its inputs.

Usage:
    python3 check_sections.py <briefing_dir>      # dir holding pack.json + sections.json
    python3 check_sections.py <briefing_dir> --json
    python3 check_sections.py --self-test

Exit code 0 when every hard check passes (warnings allowed), 1 otherwise.
Standard library only, so it runs on the GitHub runner without installs.

Checks (codes appear in the output):
  S1 schema       closed schema in references/sections-schema.json
  S2 alignment    stories / earnings / closest match the pack in count and order
  S3 length       word and sentence limits
  S4 lexicon      banned words, empty phrases, advice, puffery (references/lexicon.json)
  S5 punctuation  no em or en dashes, emoji or markdown
  S6 numbers      every number traces to pack.json or lookups.json
  S7 sources      cited headlines were read; each story names a cause or says there is no news
  S8 model        each top story gives the model's view
  S9 repetition   stories do not share opening or closing shapes
  S10 lookups     lookups.json matches its schema and the lookup budget
  W*              warnings: filler words, interpretive -ing endings, price-led titles
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parents[1]
SECTIONS_SCHEMA = SKILL / "references" / "sections-schema.json"
LOOKUPS_SCHEMA = SKILL / "references" / "lookups-schema.json"
LEXICON = SKILL / "references" / "lexicon.json"

MAX_LOOKUPS_PER_TICKER = 2
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
TIME_UNITS = ("day", "days", "week", "weeks", "month", "months", "year", "years",
              "session", "sessions", "quarter", "quarters")
NUM_RE = re.compile(r"(?<![A-Za-z0-9.])([-+−]?\d[\d,]*(?:\.\d+)?)")
# Numbers that are part of a name, not a quantity.
NAMED_NUMBER_RE = re.compile(
    r"(S&P|FTSE|Stoxx|STOXX|Nasdaq|Russell|CAC|DAX|Nikkei|Hang Seng|MSCI World)"
    r"(\s+All-World)?\s+\d+")
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


@dataclass
class Report:
    fails: list[str] = field(default_factory=list)
    warns: list[str] = field(default_factory=list)

    def fail(self, code: str, where: str, msg: str) -> None:
        self.fails.append(f"FAIL [{code}] {where}: {msg}")

    def warn(self, code: str, where: str, msg: str) -> None:
        self.warns.append(f"WARN [{code}] {where}: {msg}")

    @property
    def ok(self) -> bool:
        return not self.fails


# ---------------------------------------------------------------------------
# Small closed-schema validator (the subset our schemas use)
# ---------------------------------------------------------------------------

def validate_schema(value: Any, schema: dict, path: str, rep: Report, code: str) -> None:
    t = schema.get("type")
    if "enum" in schema and value not in schema["enum"]:
        rep.fail(code, path, f"must be one of {schema['enum']}")
        return
    if t == "object":
        if not isinstance(value, dict):
            rep.fail(code, path, "must be an object")
            return
        for key in schema.get("required", []):
            if key not in value:
                rep.fail(code, path, f"missing field '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    rep.fail(code, path, f"unexpected field '{key}'")
        for key, sub in props.items():
            if key in value:
                validate_schema(value[key], sub, f"{path}.{key}", rep, code)
    elif t == "array":
        if not isinstance(value, list):
            rep.fail(code, path, "must be a list")
            return
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            rep.fail(code, path, f"at most {schema['maxItems']} items")
        for i, item in enumerate(value):
            validate_schema(item, schema.get("items", {}), f"{path}[{i}]", rep, code)
    elif t == "string":
        if not isinstance(value, str):
            rep.fail(code, path, "must be a string")
            return
        if len(value) < schema.get("minLength", 0):
            rep.fail(code, path, f"too short ({len(value)} < {schema['minLength']} characters)")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            rep.fail(code, path, f"too long ({len(value)} > {schema['maxLength']} characters)")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            rep.fail(code, path, f"does not match {schema['pattern']}")


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z0-9][A-Za-z0-9'’.,%+\-$€£]*", text)


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'(])", text.strip())
    return [p for p in parts if p.strip()]


def contains_phrase(text: str, phrase: str) -> bool:
    return re.search(r"(?<![A-Za-z])" + re.escape(phrase.lower()) + r"(?![A-Za-z])",
                     text.lower()) is not None


def _numbers_in(obj: Any, out: list[float]) -> None:
    if isinstance(obj, bool):
        return
    if isinstance(obj, (int, float)):
        out.append(float(obj))
    elif isinstance(obj, str):
        for m in NUM_RE.finditer(obj):
            try:
                out.append(float(m.group(1).replace(",", "").replace("−", "-")))
            except ValueError:
                pass
    elif isinstance(obj, dict):
        for v in obj.values():
            _numbers_in(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _numbers_in(v, out)


def traceable(token: str, inputs: list[float]) -> bool:
    """True when |token| equals some input (or input x100 for fractions), rounded alike."""
    clean = token.replace(",", "").replace("−", "-").lstrip("+-")
    try:
        t = float(clean)
    except ValueError:
        return True
    dp = len(clean.split(".")[1]) if "." in clean else 0
    for x in inputs:
        for cand in (abs(x), abs(x) * 100 if abs(x) <= 1.5 else None):
            if cand is not None and round(cand, dp) == round(t, dp):
                return True
    return False


def exempt_number(text: str, start: int, end: int, token: str) -> bool:
    after = text[end:end + 12].lower()
    before = text[max(0, start - 6):start].lower()
    for m in NAMED_NUMBER_RE.finditer(text):
        if m.start() <= start < m.end():
            return True                                   # "S&P 500", "FTSE 100"
    if len(before) >= 2 and before[-1] == "-" and before[-2].isalpha():
        return True                                       # "GLP-1", "COVID-19"
    clean = token.replace(",", "").lstrip("+-−")
    if "." not in clean and clean.isdigit():
        n = int(clean)
        if 1900 <= n <= 2100:
            return True                                   # a year
        if after[:1] == "-" and any(after[1:].startswith(u.rstrip("s")) for u in TIME_UNITS):
            return True                                   # "10-year", "12-month"
        if n <= 31 and (after.strip().startswith(MONTHS) or before.strip().endswith(MONTHS)):
            return True                                   # "7 Oct", "Oct 7"
        if n <= 12 and any(after.lstrip().startswith(u) for u in TIME_UNITS):
            return True                                   # "three to 5 days"
    if after[:1].isalpha() and after[:1] not in "xmkb":
        return True                                       # part of a code, e.g. "2s10s"
    return False


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def check(pack: dict, sections: Any, lookups: Any, lexicon: dict,
          sections_schema: dict, lookups_schema: dict) -> Report:
    rep = Report()

    # S1 schema
    validate_schema(sections, sections_schema, "sections", rep, "S1")
    if not isinstance(sections, dict):
        return rep
    stories = sections.get("top_stories") if isinstance(sections.get("top_stories"), list) else []
    earnings = sections.get("earnings_watch") if isinstance(sections.get("earnings_watch"), list) else []
    closest = sections.get("closest_to_changing") if isinstance(sections.get("closest_to_changing"), list) else []

    # S10 lookups
    if lookups is None:
        lookups = []
    validate_schema(lookups, lookups_schema, "lookups", rep, "S10")
    if isinstance(lookups, list):
        per: dict[str, int] = {}
        for lk in lookups:
            if isinstance(lk, dict):
                per[lk.get("ticker", "")] = per.get(lk.get("ticker", ""), 0) + 1
        for tk, n in per.items():
            if n > MAX_LOOKUPS_PER_TICKER:
                rep.fail("S10", "lookups", f"{n} lookups for {tk} (budget {MAX_LOOKUPS_PER_TICKER})")
    else:
        lookups = []

    # S2 alignment
    for name, got in (("top_stories", stories), ("earnings_watch", earnings),
                      ("closest_to_changing", closest)):
        want = [e.get("key") for e in pack.get(name, [])]
        have = [it.get("ticker") for it in got if isinstance(it, dict)]
        if have != want:
            rep.fail("S2", name, f"tickers {have} must match the pack's {want}, in order")

    # Collect every text field with its location.
    texts: list[tuple[str, str]] = []
    if isinstance(sections.get("headline"), str):
        texts.append(("headline", sections["headline"]))
    if isinstance(sections.get("market_macro"), str):
        texts.append(("market_macro", sections["market_macro"]))
    for name, got in (("top_stories", stories), ("earnings_watch", earnings),
                      ("closest_to_changing", closest)):
        for i, it in enumerate(got):
            if isinstance(it, dict):
                for f in ("title", "body"):
                    if isinstance(it.get(f), str):
                        texts.append((f"{name}[{i}].{f}", it[f]))

    # S3 length
    if isinstance(sections.get("headline"), str) and len(words(sections["headline"])) > 20:
        rep.fail("S3", "headline", f"{len(words(sections['headline']))} words (max 20)")
    macro = sections.get("market_macro")
    if isinstance(macro, str):
        paras = [p for p in macro.split("\n\n") if p.strip()]
        n = len(words(macro))
        if len(paras) != 2:
            rep.fail("S3", "market_macro", f"{len(paras)} paragraphs (must be 2, split by a blank line)")
        if not 120 <= n <= 180:
            rep.fail("S3", "market_macro", f"{n} words (must be 120-180)")
    for i, it in enumerate(stories):
        if not isinstance(it, dict):
            continue
        body, title = str(it.get("body", "")), str(it.get("title", ""))
        n = len(words(body))
        if not 120 <= n <= 180:
            rep.fail("S3", f"top_stories[{i}].body", f"{n} words (must be 120-180)")
        if "\n" in body.strip():
            rep.fail("S3", f"top_stories[{i}].body", "must be one paragraph")
        if len(words(title)) > 8:
            rep.fail("S3", f"top_stories[{i}].title", f"{len(words(title))} words (max 8)")
    for name, cap in (("earnings_watch", 2), ("closest_to_changing", 1)):
        for i, it in enumerate(earnings if name == "earnings_watch" else closest):
            if isinstance(it, dict) and len(sentences(str(it.get("body", "")))) > cap:
                rep.fail("S3", f"{name}[{i}].body", f"more than {cap} sentence(s)")

    # S4 lexicon, S5 punctuation, warnings
    fail_lists = lexicon.get("fail", {})
    warn_lists = lexicon.get("warn", {})
    for where, text in texts:
        for group, phrases in fail_lists.items():
            for ph in phrases:
                if contains_phrase(text, ph):
                    rep.fail("S4", where, f"{group.replace('_', ' ')}: '{ph}'")
        if "—" in text or re.search(r"\s–\s", text):
            rep.fail("S5", where, "em or en dash; use a full stop, comma or brackets")
        if EMOJI_RE.search(text):
            rep.fail("S5", where, "emoji")
        if re.search(r"(\*\*|__|^#|`)", text, flags=re.M):
            rep.fail("S5", where, "markdown")
        for ph in warn_lists.get("filler_words", []):
            if contains_phrase(text, ph):
                rep.warn("W1", where, f"filler word '{ph}'")
        for ing in warn_lists.get("interpretive_ing_endings", []):
            if re.search(r",\s+" + ing + r"\b[^.]*\.", text, flags=re.I):
                rep.warn("W2", where, f"interpretive clause ', {ing} ...'")

    # S6 numbers
    inputs: list[float] = []
    _numbers_in(pack, inputs)
    _numbers_in(lookups, inputs)
    for where, text in texts:
        for m in NUM_RE.finditer(text):
            token = m.group(1)
            if exempt_number(text, m.start(1), m.end(1), token):
                continue
            if not traceable(token, inputs):
                ctx = text[max(0, m.start() - 30):m.end() + 30].replace("\n", " ")
                rep.fail("S6", where, f"number {token} is not in pack.json or lookups.json (…{ctx}…)")

    # S7 sources and cause, S8 model view
    pack_stories = {s.get("key"): s for s in pack.get("top_stories", [])}
    lookup_heads = {(lk.get("ticker"), str(lk.get("headline", "")).lower())
                    for lk in lookups if isinstance(lk, dict)}
    no_news = lexicon.get("no_news_phrases", [])
    for i, it in enumerate(stories):
        if not isinstance(it, dict):
            continue
        tk, body = it.get("ticker"), str(it.get("body", ""))
        ps = pack_stories.get(tk, {})
        read = {str(h.get("headline", "")).lower()
                for h in (ps.get("stories") or []) + (ps.get("headlines") or [])}
        srcs = it.get("sources") if isinstance(it.get("sources"), list) else []
        for j, src in enumerate(srcs):
            head = str((src or {}).get("headline", "")).lower()
            if head not in read and (tk, head) not in lookup_heads:
                rep.fail("S7", f"top_stories[{i}].sources[{j}]",
                         "headline is not in the pack's stories/headlines or lookups.json")
        says_no_news = any(contains_phrase(body, ph) for ph in no_news)
        if not srcs and not says_no_news:
            rep.fail("S7", f"top_stories[{i}]",
                     "no sources and no plain statement that no news explains the move")
        if srcs:
            names = {"reuters"} | {str((s or {}).get("source", "")).lower() for s in srcs}
            names.discard("rtrs")
            named = any(contains_phrase(body, nm) for nm in names if nm)
            dated = re.search(r"\b\d{1,2}\s+(" + "|".join(MONTHS) + r")", body.lower()) or \
                re.search(r"\b(" + "|".join(MONTHS) + r")[a-z]*\s+\d{1,2}\b", body.lower())
            if not (named and dated):
                rep.fail("S7", f"top_stories[{i}].body",
                         "cites sources but does not name the source and date in the text")
        if not contains_phrase(body, "model"):
            rep.fail("S8", f"top_stories[{i}].body", "does not give the model's view")
        title = str(it.get("title", ""))
        if re.search(r"\b(rises|rose|falls|fell|gains|drops|slips|climbs|edges|extends|"
                     r"pullback|rally|rallies)\b", title, flags=re.I):
            rep.warn("W3", f"top_stories[{i}].title", "reads as price-led; name the cause")

    # S9 repetition across stories
    bodies = [str(it.get("body", "")) for it in stories if isinstance(it, dict)]
    for a in range(len(bodies)):
        for b in range(a + 1, len(bodies)):
            wa, wb = words(bodies[a].lower()), words(bodies[b].lower())
            if len(wa) >= 5 and len(wb) >= 5:
                if wa[-5:] == wb[-5:]:
                    rep.fail("S9", f"top_stories[{a}] and [{b}]", "end with the same words")
                if wa[:3] == wb[:3]:
                    rep.fail("S9", f"top_stories[{a}] and [{b}]", "open with the same words")
            ea = sentences(bodies[a])[-1:] or [""]
            eb = sentences(bodies[b])[-1:] or [""]
            shape = lambda s: re.sub(r"[\d.,%]+", "#", s.lower())[:40]  # noqa: E731
            if ea[0] and shape(ea[0]) == shape(eb[0]):
                rep.fail("S9", f"top_stories[{a}] and [{b}]", "last sentences share a shape")
    return rep


def load(path: Path) -> Any:
    return json.loads(path.read_text()) if path.is_file() else None


def run_dir(d: Path) -> Report:
    pack = load(d / "pack.json")
    if pack is None:
        rep = Report()
        rep.fail("S0", str(d), "pack.json not found")
        return rep
    sections = load(d / "sections.json")
    if sections is None:
        rep = Report()
        rep.fail("S0", str(d), "sections.json not found or not valid JSON")
        return rep
    return check(pack, sections, load(d / "lookups.json"), load(LEXICON),
                 load(SECTIONS_SCHEMA), load(LOOKUPS_SCHEMA))


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _fixture() -> tuple[dict, dict]:
    pack = {
        "date": "2026-10-07",
        "top_stories": [{
            "key": "HLDN", "name": "Halden Pharma",
            "moves": {"ret_1d_pct": -3.4, "move_vs_normal": -2.1, "ret_1m_pct": -7.3},
            "confidence": 0.67, "drivers": ["P/E: 12.2x is cheap"],
            "stories": [{"date": "2026-10-07", "source": "RTRS",
                         "headline": "NMC endorses lifestyle changes over GLP-1 drugs",
                         "text": "children aged 10 to 19; 2.4 million people; adolescents 12 and older"}],
            "headlines": []}],
        "earnings_watch": [], "closest_to_changing": [],
    }
    body = ("Halden Pharma fell 3.4%, about 2x a normal day. Reuters reported on 7 Oct that the "
            "Nordic Medicines Council issued its first guidelines on obesity in children and "
            "favoured diet, exercise and counselling over weight-loss drugs. The agency said the "
            "drugs may suit adolescents aged 10 to 19 only after supervised programmes fail, and "
            "advised against them for younger children because of doubts over long-term safety. "
            "Slimra is already approved for some adolescents aged 12 and older in the United "
            "States and the European Union, so the guidance touches a small part of current "
            "sales but questions how far younger patients will add to future demand. The model "
            "holds the shares with 67% confidence and reads them as cheap at 12.2x earnings. "
            "Next, watch whether national regulators adopt the guidance.")
    macro = " ".join(["The S&P 500 closed higher on the day after a quiet session."] * 7) + \
        "\n\n" + " ".join(["The froth composite sits in the balanced band this week."] * 7)
    sections = {
        "headline": "NMC guidance on child obesity drugs weighed on Halden Pharma.",
        "market_macro": macro,
        "top_stories": [{"ticker": "HLDN", "title": "NMC guidance on child GLP-1 use",
                         "body": body,
                         "sources": [{"source": "RTRS", "date": "2026-10-07",
                                      "headline": "NMC endorses lifestyle changes over GLP-1 drugs"}]}],
        "earnings_watch": [], "closest_to_changing": [],
    }
    return pack, sections


def self_test() -> int:
    lex, ss, ls = load(LEXICON), load(SECTIONS_SCHEMA), load(LOOKUPS_SCHEMA)
    pack, good = _fixture()

    def codes(sections: dict, lookups: Any = None) -> set[str]:
        rep = check(pack, sections, lookups, lex, ss, ls)
        return {f.split("]")[0].split("[")[1] for f in rep.fails}

    assert codes(good) == set(), check(pack, good, None, lex, ss, ls).fails

    def mutate(fn: Any) -> dict:
        s = json.loads(json.dumps(good))
        fn(s)
        return s

    cases = {
        "S1": lambda s: s.update(extra="x"),
        "S2": lambda s: s["top_stories"][0].update(ticker="CRVX"),
        "S3": lambda s: s["top_stories"][0].update(body=s["top_stories"][0]["body"][:300]),
        "S4": lambda s: s.update(headline="A pivotal day for Halden Pharma and its peers."),
        "S5": lambda s: s.update(headline="Halden Pharma fell — NMC guidance weighed on it."),
        "S6": lambda s: s["top_stories"][0].update(
            body=s["top_stories"][0]["body"].replace("67%", "71%")),
        "S7": lambda s: s["top_stories"][0]["sources"][0].update(headline="Invented headline here"),
        "S8": lambda s: s["top_stories"][0].update(
            body=s["top_stories"][0]["body"].replace("The model holds", "Holders keep")),
    }
    for code, fn in cases.items():
        got = codes(mutate(fn))
        assert code in got, f"{code} not raised: {got}"
    # A story with no sources must say plainly that no news explains the move.
    no_src = mutate(lambda s: s["top_stories"][0].update(sources=[]))
    assert "S7" in codes(no_src)
    # Numbers from lookups.json count as traced.
    lk = [{"ticker": "HLDN", "tool": "news_nl_search", "source": "RTRS", "date": "2026-10-07",
           "headline": "Halden shares fall", "excerpt": "Shares fell 71% of the way to a low."}]
    assert "S6" not in codes(mutate(cases["S6"]), lk)
    # Lookup budget.
    assert "S10" in codes(good, lk * 3)
    print("self-test PASS")
    return 0


def main(argv: list[str]) -> int:
    if "--self-test" in argv:
        return self_test()
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    rep = run_dir(Path(args[0]))
    if "--json" in argv:
        print(json.dumps({"ok": rep.ok, "fails": rep.fails, "warns": rep.warns}, indent=2))
    else:
        for line in rep.fails + rep.warns:
            print(line)
        print("PASS" if rep.ok else f"FAIL ({len(rep.fails)} failures, {len(rep.warns)} warnings)")
    return 0 if rep.ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
