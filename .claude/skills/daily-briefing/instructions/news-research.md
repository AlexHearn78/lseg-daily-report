# Finding the cause of a move

For each top story, decide which of these explains the move, in this order
of preference:

1. **Company event**: results, guidance, a deal, a regulator or court
   decision, a product approval or failure, management change, a capital
   raise, an analyst action reported in the news.
2. **Sector or peer read-across**: news about a competitor, customer,
   supplier or the sector that names or clearly affects this company.
3. **Macro**: rates, currency, commodity or policy news that the stories tie
   to this stock or its sector.
4. **No identifiable news**: nothing in the pack or your lookups explains
   the move. Say so plainly. This is a valid answer; an invented cause is
   not.

A move of under about 1x a normal day (`move_vs_normal`) rarely has a
specific cause. For those, lead with the most important company news of the
past few days instead, and say the price move was ordinary.

## Step 1: read the pack

Read every item in the stock's `stories` (full text) and `headlines`.
Prefer stories that name the company in the text. For each candidate
cause, note the source, date and the specific facts (numbers, names,
decisions) it gives.

The pack's `stories` come from LSEG's `important_company_news` and text
searches restricted to the company, for the ten days to the run date, so
they usually hold the cause. Older stories in that window may predate the
move; check the date.

## Step 2: live lookups (only when the pack does not explain the move)

Use a lookup when the pack has no story naming the company, the stories
predate the move, or the move is large (2x normal or more) and the stories
do not account for it. Budget: **at most two lookups per stock and eight
per run.** Do not use lookups for stocks the pack already explains.

Tools (read-only). **Identify the company by `companyPermIds`** (the
story's `permid`): LSEG news is indexed by PermID, and a `companyRics`
query often returns nothing for the same company. Use `companyRics` only
when `permid` is null.

- `mcp__lseg__important_company_news`: the company's important news.
  Arguments: `companyPermIds`, `start` and `end` (YYYY-MM-DD; the pack
  already covers ten days before `date`, so widen only with reason), and
  `headlineOnly: false` to get story text.
- `mcp__lseg__news_nl_search`: text search. **Always restrict it with
  `companyPermIds`**: without a company filter it returns the newest
  stories on any subject. Use **one common word** in `nlQuery` that the
  story is likely to contain, such as "guidance", "downgrade", "listing",
  "approval", "deal", "results". Do not put the company's name in
  `nlQuery`: with a PermID filter that returns nothing. The pack has already
  searched "shares" and "stock". Arguments: `nlQuery`, `companyPermIds`,
  `start`, `end`, `headlineOnly: false`.

LSEG's coverage of some smaller European companies is thin. When both
tools return nothing, that is the answer: say no news explains the move.

If a tool errors or returns nothing, do not retry with variations beyond
your budget. Write the story from what you have.

## Step 3: log what you used

Every live result you rely on goes into `lookups.json` in the pack's
folder, as a JSON list:

```json
[
  {"ticker": "HLDN", "tool": "important_company_news",
   "source": "RTRS", "date": "2026-10-07",
   "headline": "NMC endorses lifestyle changes over GLP-1 drugs in child obesity fight",
   "excerpt": "The passage you relied on, copied from the result, up to 1,500 characters."}
]
```

Write `[]` if you made no lookups. The gate traces your numbers and cited
headlines to `pack.json` plus `lookups.json`, so anything you use from a
lookup must be in the excerpt.

## Weighing sources

- Reuters (`RTRS`) news stories outrank `BRIEF-` items, which are
  one-paragraph summaries of company releases. A `BRIEF-` item is still a
  good source for the fact it reports.
- A story about the sector that mentions the company in passing is
  read-across, not a company event. Say which it is.
- When two causes compete, name the one the stories tie to the stock and
  mention the other only if the stories do.
- "Analysts said", "investors fear" and similar need a named story behind
  them. Attribute views to the story: "Reuters reported that ...".
