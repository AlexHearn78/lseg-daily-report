# Using the report in Claude chat

The nightly job needs no Claude account. If you use Claude chat with the
LSEG connector, you can turn each evening's report into written commentary
and question any name against live LSEG data.

## One-time setup (5 minutes)

1. In Claude, create a **Project** called "Daily Report".
2. Add two files from this repo to the project's knowledge:
   `workflows/prompts/writing_style.md` and `docs/ARCHITECTURE.md`.
3. Make sure the **LSEG connector** is enabled for the project.
4. Paste this into the project instructions:

   > Each evening I attach the daily report (an HTML file). Treat it as the
   > source of record for that day's prices, signals, scores and risk gates.
   > When I ask for commentary, write: one headline sentence; two short
   > paragraphs on markets and the froth score; one paragraph on each of the
   > three top stories; one sentence on each earnings call in the earnings
   > watch; one sentence on each name closest to changing. Follow
   > writing_style.md. Use only facts in the report unless I ask you to look
   > something up. When you do, use the LSEG connector and say which tool
   > and date the figure came from. Describe the model's signals. Never tell
   > me to buy or sell.

## Each evening

Open the email and drag the attached `daily-report-<date>.html` into a new
chat in the project. Then, for example:

- "Write tonight's commentary."
- "Why is NG closest to changing, and what would move it to a BUY?"
- "Pull the latest IBES EPS revisions for SAP and compare them with the
  valuation score in the report."
- "Summarise the last earnings call for ASML from LSEG transcripts."

The report's numbers come from the nightly LSEG run. Anything Claude looks
up during the chat comes from the connector at that moment, so the two can
differ by the time between the run and your question.
