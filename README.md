# LSEG Daily Report

An evening equity report built entirely on LSEG data, run by GitHub
Actions on your own LSEG account and emailed as HTML.

For each name on your list, it pulls prices, news, IBES consensus,
fundamentals, the rates curve and the equity vol surface from the LSEG
hosted MCP endpoint. It scores each name on six dimensions, applies nine
risk gates and records a BUY, HOLD or SELL signal. It then builds one
report with these sections:

- **Market dashboard**: S&P 500, FTSE All-World, US 10-year yield, 2s10s
  curve, Euro Stoxx 50 put/call ratio, and a 0–100 market froth score with
  its three pillars
- **Today's moves**: the largest moves relative to each name's normal day
- **Top stories**: the three most newsworthy moves, written from the full
  text of LSEG news, with their sources and the model score that pushed
  hardest (main factor)
- **Earnings watch**: earnings calls published in the last 14 days, from
  LSEG transcripts
- **Closest to changing**: HOLDs nearest a BUY or SELL threshold
- **Decisions table**: every name's signal, confidence and risk gates, with a
  drill-down into the underlying data
- **Earnings signal (shadow)**: EPS surprise and FY1 revision after results,
  from the job's own daily IBES consensus snapshots, and what the composite
  would be with them counted

In the attached report, every tile, froth pillar and move opens the detail
behind it: 12-month price charts, the froth metrics with their dates and
10-year history, and the regime rules.

This is illustrative tooling for discussing LSEG data. It is not investment
advice, and the signals are model output.

## Set up your own copy (about 20 minutes)

You need: an LSEG service account with access to the hosted MCP endpoint
(scope `lfa`), a GitHub account, and [uv](https://docs.astral.sh/uv/) for
the one local step.

1. Click **Use this template → Create a new repository**. Make it
   **private**: its runs and artifacts hold licensed LSEG data.
2. Clone it and list your names in [`config/universe.yaml`](config/universe.yaml).
   Use the RIC of the primary listing, e.g. `NG.L`, `SAPG.DE`, `NOVOb.CO`.
   Set the report title in [`config/report.yaml`](config/report.yaml).
3. Check the names against LSEG:

   ```bash
   uv sync
   cp .env.example .env    # then fill in LSEG_CLIENT_ID and LSEG_CLIENT_SECRET
   uv run workflows/resolve_universe.py
   ```

   This writes `config/universe.resolved.json` with each company's name,
   organisation PermID (used for earnings-call transcripts) and quote
   currency. It exits with an error for any RIC LSEG cannot match. Commit
   both config files and push.
4. In the GitHub repo, open **Settings → Secrets and variables → Actions** and
   add the secrets below.
5. Open **Actions → Daily Report → Run workflow**. The first run takes
   longer, because it backfills ten years of LSEG market history and four
   years of weekly S&P 500 skew. Read the run log and the report, not just
   the green tick.

After that it runs every weekday at 20:30 UTC.

### Secrets

| Secret | Needed | What it does |
|---|---|---|
| `LSEG_CLIENT_ID`, `LSEG_CLIENT_SECRET` | **yes** | your LSEG service account. Without them nothing runs. |
| `SMTP_USER`, `SMTP_APP_PASSWORD`, `SMTP_TO` | for email | sender, app password, recipients (comma-separated). `SMTP_HOST` / `SMTP_PORT` default to Gmail. Without these the report is still built and kept as a run artifact. |
| `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` | optional | lets Claude write the commentary sections; see below |

## Commentary: three ways

1. **Built in (default).** Without Claude, the report fills the market and
   story sections with template text from the same facts.
2. **Claude chat with the LSEG connector.** Upload the `daily-briefing`
   skill once (`uv run scripts/build_chat_skill.py`), then drag the emailed
   report into Claude and ask for the commentary.
   [docs/CLAUDE_CHAT.md](docs/CLAUDE_CHAT.md) has the steps.
3. **Claude in the workflow.** Add `CLAUDE_CODE_OAUTH_TOKEN` (from
   `claude setup-token`, on your own Claude subscription) or
   `ANTHROPIC_API_KEY` (your Anthropic organisation). Each night Claude runs
   the same skill ([`.claude/skills/daily-briefing`](.claude/skills/daily-briefing))
   with read-only LSEG news tools, and a gate script checks every number and
   source before the email goes out. If the step fails, the email falls back
   to option 1.

Both Claude options follow the house style in
[`references/writing-style.md`](.claude/skills/daily-briefing/references/writing-style.md).
The skill's offline evals run with
`uv run python .claude/skills/daily-briefing/evals/run_evals.py`.

## Manual runs

```bash
# rebuild the report from the last scheduled run, without email
gh workflow run daily-report.yml -f report_only=true -f send_email=false
# market regime score only
gh workflow run daily-report.yml -f regime_only=true
# one name locally
uv run workflows/mdu_run.py --ticker NG
```

## Data handling

- Everything the job writes goes under `data/`, which is gitignored. Run
  artifacts are kept for 30 days.
- The only job-related files you commit are in `config/`. The resolved file
  holds identifiers, not market data.
- The Claude step runs with `show_full_output` off. Turning it on prints the
  facts pack, which is LSEG data, into the Actions log.
- Data is used under your own LSEG licence and stays in your GitHub account
  and email.

## How it works

[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) covers the pipeline, the
scoring and the identity and currency rules.
[docs/REGIME_FROTH.md](docs/REGIME_FROTH.md) covers the froth score.

## Known limits

- The cron runs in UTC, so the report arrives an hour earlier in UK winter.
- Per-share consensus in a different currency from the quote (for example a
  DKK listing with EUR consensus) is dropped rather than converted. Those
  names score valuation on the remaining measures.
- The froth score uses only LSEG data. There is no LSEG source for margin
  debt or a licensed credit-spread history on every account, so the score
  has three pillars (valuation, positioning, liquidity); see
  [docs/REGIME_FROTH.md](docs/REGIME_FROTH.md).

## Licence

MIT. See [LICENSE](LICENSE).
