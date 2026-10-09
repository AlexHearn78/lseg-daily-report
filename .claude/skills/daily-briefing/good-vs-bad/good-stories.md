# Good top stories

From the eval fixture (`evals/candidates/good.json`), which passes the gate.
Each follows move, cause, meaning and model view, watch.

## HLDN: "NMC guidance on child GLP-1 use"

> Halden Pharma fell 3.4%, about 2x a normal day, extending a 7.5% slide over
> the week. Reuters reported on 7 Oct that the Nordic Medicines Council
> issued its first global guidelines on obesity in children, putting diet,
> exercise and counselling ahead of weight-loss drugs. The agency said GLP-1
> drugs should reach adolescents aged 10 to 19 only after supervised
> programmes fail, and advised against medication for children under 10 over
> doubts about long-term safety. Slimra is already approved for some
> adolescents aged 12 and older in the United States and the European Union,
> so the guidance touches a small slice of current sales. Its weight is on
> assumptions about future demand from younger patients, where the NMC now
> sets a cautious line. The model still reads the shares as cheap at 12.2x
> earnings and holds them with 67% confidence. Watch whether national
> regulators adopt the guidance.

Why it works:

- One sentence on the move; the chart in the report does the rest.
- The cause is specific, dated and attributed, with the facts that matter
  (ages, conditions, where Slimra is approved).
- It weighs the news: small effect on current sales, a question over future
  demand. Every fact in that judgement is in the story.
- The model's view reads as the model's, with the driver that matters.
- It ends on a concrete thing to watch.

## LNDQ: an ordinary move with no news behind it

> Lindqvist rose 2.2%, about 1.3x a normal day, and is up 4.1% over the week.
> No company news explains the move. The latest item on the company is a
> refinancing reported on 6 Oct ...

Why it works: it says plainly that nothing explains the move, then uses
the space for the most relevant recent company news and what it means,
instead of inventing a reason (`evals/candidates/no_news_story.json`).

## When the pack is not enough

`evals/candidates/lookup_supported.json` cites a broker upgrade found with a
live `news_nl_search` lookup. The result is saved in `lookups.json`, so the
cited headline and the 165 euro target trace to a logged source and the
report shows it under Sources.
