# Writing a top story

One story per item in `top_stories`, in the same order. **120 to 180
words.** One paragraph, no line breaks.

## Structure

1. **The move, in one sentence.** Day move and how unusual it was
   (`move_vs_normal`), plus the week or month only if it changes the
   picture. The report already shows the chart, so keep this short.
2. **The cause.** The specific event, with its source and date in the text
   ("Reuters reported on 7 Oct that ..."). Give the facts that matter: the
   numbers, the decision, who said what. If the cause is read-across or
   macro, say so. If no news explains the move, say that in one sentence
   and spend the space on the most relevant recent company news instead.
3. **What it means.** The bearing on the investment case: revenue, margins,
   demand, regulation, balance sheet, competitive position. Use the facts in
   the stories; do not speculate beyond them. Then the model's view, worked
   in naturally: its signal, its confidence, and the drivers that matter
   here. Phrase it differently in each story.
4. **What to watch.** One short sentence naming the next concrete thing the
   stories point to (a decision date, a results date, a regulator, a
   pending deal). Leave it out if the stories give nothing specific.

Roughly: move 15%, cause 40%, meaning and model 35%, watch 10%.

## Title

At most eight words. **Name the cause, not the price.**

- Good: "NMC guidance on child GLP-1 use hits sentiment"
- Good: "DOJ probe into Vantir licensing deal"
- Bad: "Halden Pharma extends pullback despite Japanese approval" (leads with
  price, buries the cause)
- Bad: "Corvex rises on light news"

When no news explains the move: "No news behind a 2x move" or similar.

## sources

List the stories the cause and meaning rest on (at most three), each as
`{"source": "RTRS", "date": "YYYY-MM-DD", "headline": "exact headline"}`,
copied exactly from the pack or `lookups.json`. Empty list when no news
explains the move.

## Check each story

- Would the reader learn why the stock moved, beyond the number?
- Is every fact in the pack or `lookups.json`?
- Is the cause dated and attributed in the text?
- Does the model's view read as the model's, not as advice?
- Does it end on a concrete fact, not a verdict?
