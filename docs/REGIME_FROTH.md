# Market regime and froth score

A market-wide 0–100 froth dial, shown on the report's dashboard and used by
the analyst's risk gate 5 (allocation caps). **Every input comes from LSEG.**
High always means more stretched.

## Inputs

| Pillar (weight) | Metric | LSEG source | Frothy when |
|---|---|---|---|
| Valuation (0.27) | `spx_stretch`: S&P 500 vs its 200-day average, % | `.SPX` daily close | high |
| Positioning (0.40) | `lseg_spx_skew`: 90% minus ATM implied vol, ~3-month expiry | `equity_vol_surface` on `.SPX` | low |
| | `eurex_putcall_sx5e`: Euro Stoxx 50 put/call ratio | `.PRSTXE.EX` (Eurex) | low |
| Liquidity (0.33) | `funding_spread`: SOFR minus effective fed funds | `USDSOFR=`, `USONFFE=FEDR` fixings | low |
| | `real_policy_rate`: effective fed funds minus US CPI YoY | `USONFFE=FEDR`; `qa_macroeconomic` `USCONPRCE` | low |

Each metric is ranked against its own history (percentile, 0–100). A pillar
is the average of its metrics; the composite is the weighted average of the
pillars present (weights renormalise if one is missing). *Structural* is the
valuation pillar; *timing* is the average of positioning and liquidity.
Bands: under 20 capitulation, 20–40 risk off, 40–60 balanced, 60–80
elevated, 80+ frothy. Velocity compares today with about 90 days ago.

A metric whose latest observation is more than 14 days old is left out and
listed under `stale_metrics`. Each CPI print is used from 45 days after the
month it measures, when it is actually published.

## What changed from the original design, and why

The original score used FRED, FINRA, CFTC, CBOE, ICI and Federal Reserve
Z.1 data. The template uses LSEG only, so:

- **Leverage pillar removed.** There is no LSEG source for margin debt or
  broker margin loans. Its weight is spread proportionally over the other
  three pillars.
- **Valuation uses price stretch, not credit spreads.** High-yield indices
  (ICE BofA, FTSE) are on LSEG but need a separate index licence, and LSEG
  credit curves have no history. If your account carries one of those
  licences, a credit-spread metric can be added in
  `regime/sources/lseg.py` and `regime/daily.py`.
- **Positioning uses Eurex put/call** in place of CBOE put/call, CFTC
  futures positioning and ICI fund flows.

## Dashboard tiles

S&P 500 (`.SPX`), FTSE All-World (`.FTAWORLDSR`), US 10-year and 2s10s
(YieldBook US government curve via `fixed_income_curves`), and the Euro
Stoxx 50 put/call ratio. Each tile opens a 12-month chart in the attached
report.

## History and runs

- `data/raw/regime/history/<series>.csv`, kept between runs in the Actions
  cache.
- The first run backfills ten years of each series (Treasury yields weekly,
  then daily) and four years of weekly S&P 500 skew. Later runs fetch only
  the days since the last observation.
- `workflows/regime_froth.py` refreshes and scores, writing
  `data/outputs/regime/<date>/froth_score.json`. `workflows/regime_backfill.py`
  replays the skew history and writes a coverage manifest.

Manual run without the batch or email:

```bash
gh workflow run daily-report.yml -f regime_only=true
```
