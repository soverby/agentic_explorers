---
name: usage-review
description: Weekly token usage by model and effort from local Claude Code transcripts, with ranked recommendations to cut tokens without quality loss or to raise quality.
disable-model-invocation: true
argument-hint: "[--start YYYY-MM-DD] [--end YYYY-MM-DD] [--profile NAME=PATH ...] [--out DIR] [--changes PATH]"
---

Review my Claude Code token use and recommend changes. Change no files except the report. Do not quote transcript content.

1. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/usage_report.py" $ARGUMENTS` (use `python` if `python3` is not found). The last line of its output names the output directory.
2. If the exit code is 2 or 3, stop and report the error message to me. Do not patch or rewrite the script.
3. Read `findings.json`, `inventory.json` and `report.md` in the output directory. Use `diagnostics.json` for detail.
4. Before you recommend a setting, check the current Claude Code docs at code.claude.com/docs for it. Record the page URL and today's date next to the item.
5. Append a `## Recommendations` section to `report.md` with two ranked lists:
   - A. Fewer tokens, no quality loss.
   - B. Better quality, with the token cost.

   Rank by the finding `score`. Every item must have:
   - the finding id it is based on;
   - the exact change: file, key and value (or text);
   - the quality risk (none, low or medium) and the reason;
   - how to re-check it later (the metric, and a `changes.json` entry with its date).

   Mark items that do not come from a measured finding as inferred. Do not change any other file.
