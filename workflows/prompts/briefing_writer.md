# Nightly briefing writer (automated run)

Write the narrative sections of tonight's daily report from the briefing
pack named in your task.

Use the skill in `.claude/skills/daily-briefing/`. Read its `SKILL.md` and
every file it lists before writing. All of its non-negotiable controls
apply.

## Already done by the pipeline

`workflows/mdu_briefing.py` has built, in the folder named in your task:

- `pack.json`: the facts, including the full text of the top stories' news.
- `charts.json`: price series for the report's charts. You do not need it.
- `sections_template.json`: plain fallback text. Do not edit it.

## Your steps

1. Follow the skill's workflow: read the pack, find the cause of each top
   story's move (live LSEG news lookups only when the pack does not explain
   it, within the budget), and write `sections.json`.
2. Write `lookups.json` in the same folder, `[]` if you made no lookups.
3. Run the gate until it prints `PASS`, at most three rounds:

   ```
   python3 .claude/skills/daily-briefing/scripts/check_sections.py <folder>
   ```

4. Stop. Do not create or change any other file. Do not commit.
