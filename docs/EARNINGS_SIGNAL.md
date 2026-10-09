# Earnings signal (phase 1: shadow mode)

Adds how a long-only analyst reads a results announcement to the daily
model, first in shadow mode: the report shows what the composite *would* be
with earnings counted, but ratings are unchanged until the signal has been
reviewed over an earnings season.

## Why a consensus history

`qa_ibes_consensus` returns only the latest published consensus. Its
"historical" periods are past fiscal periods, not past dates, so there is no
way to ask LSEG what consensus was the day before results. The daily job
now stores a snapshot for every ticker (`workflows/consensus_snapshot.py`,
one batched IBES call each, by RIC, ~8 s):

- quarterly actuals (EPS, revenue) with the IBES `announceDateUTC`
- next-quarter and current-quarter consensus
- FY1 and FY2 consensus (EPS, revenue)

Rows go to `data/raw/consensus/<TICKER>.csv`, kept between runs in the
Actions cache (`consensus-history-*`).

## The score (`lseg_quant.signals.earnings`)

Active from results day to 60 trading days after.

| Component | Weight | Input | Full +/-1 at |
|---|---|---|---|
| Estimate revisions | 40% | FY1 and FY2 EPS now vs the last pre-results snapshot | +/-5% |
| Surprise | 25% | EPS and revenue vs the last pre-results snapshot | EPS +/-10%, revenue +/-5% |
| Guidance | 25% | raised / kept / cut, from the earnings-call analysis | (not wired yet) |
| Tone | 10% | sentiment and hedging change vs prior call | (not wired yet) |

Weights renormalise over the components available. The score would carry
20% of the composite on results day, fading linearly to 0 at 60 trading
days: `shadow = composite x (1 - w) + w x score`.

Until the history covers a results date, surprise falls back to the latest
published consensus (marked `*` in the report) and revisions show n/a. A
new copy of the template starts collecting snapshots on its first run, so
the signal becomes fully informed from the first results after that date.

## Report

- **Earnings signal (shadow)** card: days since results, EPS surprise, FY1
  revision, earnings score, composite and composite with earnings; a rating
  that would change is highlighted.
- **Main factor** on each top story: the model score (valuation, trend,
  risk, macro, news, volatility) with the largest weighted push on the
  composite (weight x score), its direction and its share of the total push.

## Next phases

1. Backtest on past quarters (IBES actuals history; revisions from later
   snapshots) against 20- and 60-day returns.
2. Switch on with the weight and fade the evidence supports.
3. Guidance and tone from an earnings-call analysis as inputs.
