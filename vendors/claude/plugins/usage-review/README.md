# usage-review

A Claude Code skill that analyses your local Claude Code transcripts. It reports weekly token use by model and effort, runs diagnostics on cache use, context size, compaction and subagents, and gives two ranked lists of recommendations: reduce tokens with no quality loss, and improve quality. It does not change any file. All output stays on your machine.

This plugin is Claude Code-specific: it reads the Claude Code transcript format (`~/.claude/projects/**/*.jsonl`).

Uses Claude Code-only frontmatter (`disable-model-invocation`, `argument-hint`), so it is not a portable Agent Skill.

## Install

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install usage-review@agentic-explorers
```

## Usage

The skill has `disable-model-invocation: true`, so Claude does not start it automatically. Run it yourself:

```text
/usage-review                          # all available data
/usage-review 2026-09-01 2026-09-27    # date range, YYYY-MM-DD
```

Output goes to `~/Documents/claude-usage/<today>/`: the analysis script, the weekly CSV tables, and the report.

## Requirements

The skill was written for the author's setup and refers to the items below. Some exist only on the author's machine. Edit the skill to match your setup if needed.

- `~/.claude/projects/` (Claude Code transcripts). Required.
- `~/.claude-work/projects/`: a second Claude Code profile (config directory). Optional.
- `~/Documents/claude-usage/`: output directory; earlier runs there are reused and compared.
- The `claude-api` skill, for current per-model prices and cache multipliers.
- Web access to `code.claude.com/docs`, to check current setting defaults.
- `~/.claude/bin/parity-check.sh`: the author's script that checks that the two profiles' `settings.json` files match. Not included.
- The before/after comparison names two changes to the author's configuration (2026-09-22 subagent cache TTL set to 1h; 2026-09-23 `autoCompactWindow: 500000`) and a memory note `autocompact-500k-trial.md` under `~/.claude/projects/<project>/memory/`. Replace these with your own changes, or ignore that section.
- Python 3, to run the analysis script that the skill writes.

## Tested with

| Agent | Version | Model |
|---|---|---|
| Claude Code | 2.1.283 | not recorded |

The plugin manifest and the marketplace entry pass `claude plugin validate` on Claude Code 2.1.283.

## Author

Sean Overby ([@soverby](https://github.com/soverby))
