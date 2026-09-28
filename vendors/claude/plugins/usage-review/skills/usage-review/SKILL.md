---
name: usage-review
description: Weekly token usage by model and effort from local Claude Code transcripts, with ranked recommendations to cut tokens without quality loss or to raise quality.
disable-model-invocation: true
argument-hint: "[--start YYYY-MM-DD] [--end YYYY-MM-DD] [--profile NAME=PATH ...] [--out DIR] [--changes PATH]"
---

Review my Claude Code token use and recommend changes. Change no files except `recommendations.md` in the output directory. Do not quote transcript content.

1. Run `python3 "${CLAUDE_SKILL_DIR}/scripts/usage_report.py" $ARGUMENTS` (use `python` if `python3` is not found). Its last stdout line is `OK: ... -> DIR`; DIR is the output directory.
2. If the exit code is not 0, stop and report stderr to me. Do not patch or rewrite the script.
3. Read `findings.json`, `inventory.json` and `report.md` in DIR. Use `diagnostics.json` for detail. Do not change any of the script's output files.
4. Before you recommend a setting, check the current Claude Code docs at code.claude.com/docs for it. Record the page URL and today's date next to the item.
5. Write `DIR/recommendations.md` with two ranked lists:
   - A. Fewer tokens, no quality loss: findings with `"list": "A"`.
   - B. Better quality, with the token cost: findings with `"list": "B"`.

   Rank each list by the finding `score`. Every item must have:
   - the finding id it is based on;
   - the exact change: file, key and value (or text);
   - the quality risk (none, low or medium) and the reason;
   - how to re-check it later (the metric, and a `changes.json` entry with its date).

   Mark items that come from an `inferred` finding as inferred.
