# Rubric for grading a real briefing run

Use on a real run's folder (`pack.json`, `sections.json`, `lookups.json`),
after `python3 evals/run_evals.py --run <dir>`. The gate covers what a
script can prove. This rubric covers judgement.

## Hard fails (any one fails the run)

1. The gate (`check_sections.py`) does not print `PASS`.
2. A top story gives a cause that the cited story does not support, or
   attributes a view to a source that does not hold it.
3. A top story states a model driver as a market fact (for example a
   52-week-range claim taken from `drivers`).
4. A story reads as advice to buy or sell.
5. A story reproduces more than a few words of a news story verbatim.

## Weighted score (out of 100)

| Weight | Criterion | 0 | Full marks |
|---|---|---|---|
| 30 | **Cause.** Does each story explain why the stock moved, with the specific event, its source and date? | Price recap only | Specific event, facts from the story, dated and attributed; or an honest "no news" with the most relevant recent news instead |
| 20 | **Meaning.** Does it say what the news means for the investment case (revenue, margins, demand, regulation, balance sheet)? | Absent or generic | Concrete link from the news to the business, without speculation beyond the stories |
| 15 | **Model view.** Is the model's signal, confidence and the relevant driver worked in naturally, and phrased differently across stories? | Missing or bolted on | Integrated, varied, clearly the model's view |
| 10 | **Watch item.** Is there one concrete next thing to watch, drawn from the stories? | Missing or vague ("monitor developments") | Specific date, decision or event |
| 10 | **Market paragraph.** Moves for both indices, rates and credit, with reasons tied to headlines | Restated numbers only | Numbers plus attributed reasons |
| 10 | **Style.** House rules: lead with the fact, concrete, active, no slop | Several breaches | Clean |
| 5 | **Titles.** Name the cause, not the price | Price-led | Cause-led |

80 or more is a good run. Below 60, review `lookups.json` and the pack's
`stories` to see whether the problem is the inputs (thin news) or the
writing.
