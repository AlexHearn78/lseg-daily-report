# Architecture

Read this before changing code. Keep it to about a page.

## Pipeline

One GitHub Actions workflow, `.github/workflows/daily-report.yml`, runs on
weekdays at 20:30 UTC.

| Stage | Script | Output under `data/outputs/` |
|---|---|---|
| 0. Market regime | `workflows/regime_froth.py`: S&P 500, SOFR, fed funds, US CPI, Treasury curve, Euro Stoxx 50 put/call and S&P 500 skew, all from LSEG | `regime/<date>/froth_score.json` |
| 1. Analyst batch | `workflows/mdu_batch.py` runs `mdu_run.py` once per name, each in its own subprocess | `mdu/<YYYYMMDD>/audit_*.jsonl`, `narrative_<KEY>.md` |
| 2. Briefing pack | `workflows/mdu_briefing.py`: price moves, LSEG news, earnings-call transcripts, regime | `briefing/<YYYYMMDD>/pack.json`, `sections_template.json` |
| 3. Commentary (optional) | `anthropics/claude-code-action` runs the `daily-briefing` skill with read-only LSEG news tools; `check_sections.py` gates the result | `briefing/<YYYYMMDD>/sections.json`, `lookups.json` |
| 4. Report and email | `workflows/mdu_report.py`, `workflows/mdu_email.py` | `data/reports/Daily EQ Research/<date>.html` |

Every stage after 0 still runs if an earlier one fails, so a bad night still
produces an email that says what failed.

## Per-name analyst (`mdu_run.py`)

1. **Data**: all through `lseg_quant.mdu.mcp_client.MCPClient` against the
   LSEG hosted MCP endpoint (OAuth2 client credentials, scope `lfa`):
   500 daily prices, USD SOFR curve, equity vol surface, 30 days of news,
   IBES consensus (FY1+), and last year's fundamentals.
2. **Features**: trend, volatility, drawdown, macro regime, news shock
   (`mdu/features.py`).
3. **Scores**: six dimensions in [-1, 1] (`mdu/scoring.py`): valuation 0.30,
   trend 0.20, risk 0.15, macro 0.15, news 0.10, vol 0.10. A composite of
   +0.25 or more is BUY, -0.25 or less is SELL, anything else is HOLD
   (`mdu/analyst.py`).
4. **Risk gates** (`mdu/overrides.py`): data quality, confidence, news
   shock, drawdown, risk-off and froth caps, valuation versus history,
   analyst dispersion, name cap, liquidity. Each gate that fires is
   recorded and shown against the name in the report.
5. **Paper position** (`mdu/execution.py`): fills the final allocation
   from a notional 100,000. No orders are ever sent.

## Identity and currency rules

- **One identifier: the RIC.** `config/universe.yaml` lists RICs. IBES
  consensus is requested with `tickerType: RIC`, and fundamentals with
  `type: RIC`. A bare ticker such as `NG` matches several companies or none.
- **`workflows/resolve_universe.py`** checks every RIC with
  `symbology_lookup` and stores the company name, organisation PermID and
  quote currency in `config/universe.resolved.json`. The transcripts tool
  needs the PermID. An entry whose RIC no longer matches `universe.yaml`
  is ignored.
- **Currency** (`mdu/currency.py`): IBES per-share values come in the
  reporting currency. GBP consensus is rescaled to the GBp quote. Any other
  mismatch drops the per-share values and records
  `consensus_currency_mismatch` in the audit, so no P/E is computed from
  mixed units.

## Layout

```
config/                  universe.yaml, report.yaml, universe.resolved.json
src/lseg_quant/mdu/      per-name analyst: data, features, scoring, gates, audit
src/lseg_quant/regime/   froth score: sources, history store, scoring
src/lseg_quant/briefing/ facts pack and commentary rendering
src/lseg_quant/reporting/ house style for the HTML report and email
workflows/               entry-point scripts and the commentary prompts
tests/                   pytest, synthetic data only; fixtures/config is the test universe
```

## Rules

- Library code in `src/` uses `logging`, not `print`.
- No LSEG data in git, tests or logs. Tests use synthetic fixtures.
- Never hard-code a calculation date. Use `--date` or today.
- Change the universe only through `config/universe.yaml`, then re-run
  `resolve_universe.py`.
