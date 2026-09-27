---
name: usage-review
description: Weekly token usage by model and effort from local Claude Code transcripts, with ranked recommendations to cut tokens without quality loss or to raise quality.
disable-model-invocation: true
argument-hint: "[start-date] [end-date] (optional, YYYY-MM-DD; default: all data)"
---

Analyze my local Claude Code history for token use and quality, then recommend changes. Do not change any file; I will approve changes separately.

Date range: $ARGUMENTS (if empty, use all available data).

## Script reuse
Check `~/Documents/claude-usage/` for the most recent `usage_report.py`. If one exists, read it, fix it if the transcript format changed, and reuse it. Otherwise write a new one. Save the script used with this run's output.

## Data
- Sources: `~/.claude/projects/**/*.jsonl` and `~/.claude-work/projects/**/*.jsonl`, including `*/subagents/*.jsonl`. Label each row by profile (claude / claude-work) and main vs subagent (`isSidechain` or the subagents path).
- Per assistant line: `timestamp`, `message.model`, `effort`, `perTurnEffort` (use it when non-null, else `effort`; "unknown" if both absent), `message.usage` (`input_tokens`, `cache_creation_input_tokens` with its `ephemeral_5m`/`ephemeral_1h` split, `cache_read_input_tokens`, `output_tokens`, `output_tokens_details.thinking_tokens`), `cwd`, `session_id`.
- Deduplicate: one API response can be written as several lines with the same usage. Count each `message.id` (fall back to `requestId`) once. Skip `<synthetic>` models.
- Weeks: ISO weeks, Monday start, local time zone. Mark the first and last weeks as partial.
- Do not quote transcript content in the output. Some transcripts contain financial data. Keep all output local.

## Output 1: weekly table
One row per week × model × effort, sorted by week, then total tokens. Columns: week, model, effort, sessions, API calls, input (uncached), cache write 5m, cache write 1h, cache read, output, thinking, cache-hit %, and cost-weighted input. For the weights, get the current per-model prices and cache multipliers from the claude-api skill; do not use memory. Add weekly subtotals and a grand total. Put a second table below it: the same totals split by main vs subagent and by agent type where the subagent file shows it (builder / reviewer / tooling / Explore / other). Save both as CSV in `~/Documents/claude-usage/<today>/`, together with the script.

## Output 2: diagnostics (use numbers, not impressions)
- Per session: peak context (input + cache read + cache write for the largest call), number of calls, `compact_boundary` events (auto vs manual, pre/post tokens).
- Before/after comparison for known changes: 2026-09-22 subagent cache TTL set to 1h; 2026-09-23 `autoCompactWindow: 500000` (see memory note `autocompact-500k-trial.md` under `~/.claude/projects/<project>/memory/`). Also compare against any earlier `~/Documents/claude-usage/*/` run. Show the effect on cache writes, cache-hit %, and peak context. Say if the sample is too small to conclude.
- Where input tokens come from: the fixed context at session start (system prompt, CLAUDE.md, memory, skill listings, MCP tool listings, agent definitions); its size in tokens; and the top 10 largest tool results by tool type.
- Waste patterns: the same file read many times in one session; large Bash/MCP outputs; subagents that re-read context the parent already had; cache rewrites after idle gaps (>5 min for 5m, >1h for 1h); sessions that ended near the context limit; runs that stopped when the usage limit was hit.
- Subagents: tokens per builder/reviewer round, number of rounds, how often a reviewer rejected work, and the most expensive subagent runs.

## Output 3: recommendations
Two separate ranked lists:
A. Reduce tokens with no quality loss.
B. Improve quality (token cost may go up; state by how much).
Cover: CLAUDE.md (global and project), memory files, skills, agent definitions (model, effort, tools, cacheTtl), settings.json (both profiles; keep `~/.claude/bin/parity-check.sh` passing), hooks, MCP servers, compaction and context settings, and working habits (for example, when to /clear or delegate).
For each item: the evidence (a number from above), the exact change (file, key, value or text), estimated weekly token effect, quality risk (none/low/medium, with the reason), and how to check it later from the transcripts. Before you recommend a setting, check the current Claude Code docs for it (code.claude.com/docs), because the defaults change with new models. Rank by weekly effect × confidence. Separate items that are measured from items that are inferred.

## Validation
Before you report, check the totals with a second, independent method for one full week (for example, a raw sum without deduplication, then explain the difference). Report the files and date range covered, the lines skipped and why, and any field that was missing.
