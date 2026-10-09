# Bad top stories

Each of these fails the gate or the rubric. The first is the style the
report used before this skill.

## Price recap with no cause (`evals/candidates/price_only.json`)

> **Halden Pharma extends pullback**
>
> Halden Pharma fell 3.4% on the day, about twice its normal daily swing, and
> is now down 7.5% over the week and 7.3% over the month. The model still
> reads the stock as cheap on a 12.2x price-to-earnings multiple and treats
> the pullback as mild. It remains a hold, with 67% confidence.

Why it fails:

- It restates numbers the report already shows and never says **why** the
  stock fell. The NMC guidance was in the pack.
- No source, and no statement that no news explains the move (gate S7).
- 52 words, under the 120-word floor (S3).
- The title leads with the price (W3).
- Every story ends "It remains a hold, with X% confidence." (S9).

## A plausible cause nobody reported (`invented_source.json`)

Citing "Corvex faces EU antitrust charge over chip bundling" when no such
story is in the pack or `lookups.json`. A believable invented headline is
worse than no headline: the reader cannot tell it apart from a real one.
The gate rejects it (S7) and the report would not show it.

## A number from memory (`invented_number.json`)

"Halden Pharma fell 3.9%" when the pack says 3.4. Every number must trace to
the inputs (S6).

## Model drivers stated as market facts

> Leaving the shares at the bottom of their 52-week range.

This came from a model driver, not from price data. Drivers are the model's
reasoning; write "the model reads ...", and take price facts only from
`moves` (rubric hard fail 3).

## Advice and style (`advice.json`, `house_style.json`)

"Investors should buy the dip" is advice (S4). "This marks a pivotal moment
amid regulatory scrutiny — watch the regulators" breaks three house rules
at once: puffery, an empty word and an em dash (S4, S5).
