# Using the report in Claude chat

The nightly job needs no Claude account. If you use Claude chat with the
LSEG connector, the `daily-briefing` skill turns each evening's report into
written commentary, checks every number and source, and lets you question
any name against live LSEG data.

It is the same skill the nightly job can run in GitHub Actions, packaged for
chat. Each attached report carries its facts pack, so the skill works from
exactly what the nightly run saw.

## One-time setup (5 minutes)

1. Build the skill and upload it:

   ```bash
   uv run scripts/build_chat_skill.py
   ```

   In Claude, open **Settings → Capabilities**. Make sure code execution is
   on, then under **Skills** upload `dist/daily-briefing.zip`.
2. Make sure the **LSEG connector** is enabled. The skill uses its
   `important_company_news` and `news_nl_search` tools when the report's own
   news does not explain a move.

## Each evening

Open the email, drag the attached `daily-report-<date>.html` into a new
chat, and ask, for example:

- "Write tonight's commentary."
- "Why did Halden Pharma fall, and is there anything newer on it?"
- "Pull the latest IBES EPS revisions for SAP and compare them with the
  valuation score in the report."

The skill pulls the pack out of the report, writes the commentary, runs the
same gate as the nightly job and ends with its verdict.

The report's numbers come from the nightly run. Anything Claude looks up
during the chat comes from the LSEG connector at that moment, and the skill
says which tool and date each new figure came from.
