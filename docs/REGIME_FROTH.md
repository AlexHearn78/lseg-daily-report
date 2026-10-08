# Regime Froth Score — data pipeline reference

Independent, market-wide 0–100 froth dial used by the analyst batch
(Gate 5 tiered caps) and shown in the report's market dashboard.

## Layout

| Path | Role |
|---|---|
| `src/lseg_quant/regime/score.py` | percentile/min-max scoring, pillar weights, composite |
| `src/lseg_quant/regime/history.py` | CSV history store (`data/raw/regime/history/<series>.csv`) |
| `src/lseg_quant/regime/sources/` | fred / finra / cftc / cboe / ici fetchers |
| `src/lseg_quant/regime/lseg_feeds.py` | qa_macroeconomic wrapper + SPX skew (`equity_vol_surface`) |
| `src/lseg_quant/regime/market_regime.py` | market-level risk-off regime classifier (Phase 3 input) |
| `workflows/regime_backfill.py` | one-time full-history backfill + manifest |

## Metric wiring

Weights: valuation .20, leverage .25, positioning .30, liquidity .25.
Inverted series (low raw value = high froth): `hy_oas`, `cboe_putcall*`,
`lseg_spx_skew`, `funding_spread`, `real_policy_rate`.

## Source status (verified 2026-08-23)

| Series | Cadence | Coverage | Status |
|---|---|---|---|
| FRED macro (sofr, effr, cpi_yoy, breakeven_10y, hy_oas) + derived | daily | 2003–now | ✅ primary |
| `spx_close` | daily | ~10y (FRED cap) | ✅ |
| `margin_loans_z1` (BOGZ1FL663067003Q) | quarterly | 1945–now | ✅ leverage fallback for FINRA |
| `excess_leverage` (margin YoY − SPX YoY) | quarterly | Z.1 ∩ SPX ≈ 2018–2025 | ✅ thin but valid |
| `cftc_net_spec` (S&P 500 consolidated COT) | weekly | 2010–now | ✅ |
| `lseg_spx_skew` (IV 90% − IV 100% @ ~3M) | weekly replay | 3y rolling | ✅ **positioning primary**; backfilled via historical calculation dates on `equity_vol_surface` |
| `cboe_putcall_monthly` | monthly | Jan–Aug 2019 only | ⚠️ archive path serves 2019 files only; kept as supplement |
| `cboe_putcall` (daily) | daily | — | ❌ Akamai-blocked CDN/OCC; code ready if a pipe appears |
| `margin_debit_balances` (FINRA xlsx) | monthly | — | ❌ bot-protected; manual monthly download → `data/raw/regime/finra/margin-statistics.xlsx` is the sanctioned path; do not automate past it |
| `ici_equity_flows` | weekly | — | ❌ ici.org 403s non-browser clients; code ready (`pd.read_html`), marked optional per spec |

Pillar degradation is graceful: any pillar with ≥1 live metric scores normally;
missing pillars land in `low_confidence_pillars`. A metric whose latest
observation is older than its cadence allows (`daily.MAX_STALENESS_DAYS`:
14d daily, 30d weekly, 90d monthly, 400d quarterly) is excluded and listed in
`stale_metrics` — e.g. the 2019-only `cboe_putcall_monthly`.

## Commands

```bash
# full backfill (~4 min; LSEG portion = ~156 MCP calls)
uv run workflows/regime_backfill.py
# subsets
uv run workflows/regime_backfill.py --only fred,cftc,lseg_skew --skew-years 3
```

Outputs: coverage table to stdout, manifest +
reconstructed-composite CSV under `data/outputs/regime_backfill/<ts>/`.
Latest reconstructed composite at time of writing: **57.8 / 100**.

## Daily job integration (Phase 3, complete)

1. `src/lseg_quant/regime/daily.py`: payload assembly (`compute_froth_payload`,
   `compute_regime_payload`), stale-aware loader (`load_latest_context`,
   max_age_days=4), feature-layer reducer, LLM prompt block.
2. `workflows/regime_froth.py`: refreshes sources → scores → writes
   `data/outputs/regime/<date>/froth_score.json`. Runs as error-isolated
   Stage 0 of the daily workflow; `--score-only` / `--skip-skew` flags available.
3. `features.compute_features(market_context=...)`: market-wide regime now
   replaces the per-ticker `_macro_regime` price proxy when context exists;
   adds `froth_composite/band/velocity` keys.
4. Gate 5 tiered caps (`overrides.RiskOverrideConfig`): froth ≥80 → cap 15%
   regardless of regime; risk-off + froth ≤20 → cap lifted to 60%;
   risk-off otherwise → legacy 30%. No score → legacy behaviour.
5. The email report
   gains a colour-coded FROTH chip next to REGIME.
6. GitHub Actions (`.github/workflows/daily-report.yml`) runs the same Stage 0
   before the batch (`--skip-cboe`, needs the `FRED_API_KEY` secret). The
   history store persists in the Actions cache; an empty cache triggers a
   4-year LSEG skew backfill (~4 min). Manual test without the batch/email:
   `gh workflow run daily-report.yml --ref <branch> -f regime_only=true`.
