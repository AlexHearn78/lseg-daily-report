# The other sections

## headline

One sentence, at most 20 words: the single most important thing tonight.
Prefer a cause over a price ("A US probe into Corvex's Vantir deal led the
day's news" beats "Corvex fell 0.03%").

## market_macro

Two paragraphs, **120 to 180 words in total**, separated by a blank line
(`\n\n`).

- **Paragraph 1: markets and why.** The S&P 500 and FTSE All-World day and
  month moves (`macro.sp500`, `macro.ftse_all_world`), the 10-year yield,
  the 2s10s curve and the Euro Stoxx 50 put/call ratio (`macro.eurex_putcall`),
  then the likely reasons from
  `macro_headlines`. Attribute reasons to the headlines; if they do not
  explain the move, do not invent one.
- **Paragraph 2: risk appetite.** The froth composite and band, velocity,
  which pillars (`macro.froth.pillar_scores`) are stretched (high) or calm
  (low), and the regime with its reason. State what the numbers say; the
  report has a click-through with the detail, so do not explain the method.

## earnings_watch

One entry per item in `earnings_watch`, same order: **one or two
sentences** with the main takeaway from its `excerpts` (guidance, margins,
demand, tone). Paraphrase. Empty list when there are none.

## closest_to_changing

One entry per item in `closest_to_changing`, same order: **one sentence**
on how far the composite is from the signal (`gap`, `nearest_signal`,
`thresholds`) and which driver would have to change. Empty list when there
are none.
