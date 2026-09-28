# usage-review

A Claude Code skill that analyses your local Claude Code transcripts. It reports weekly token use by model and effort, runs diagnostics on cache use, context size, compaction and subagents, and gives two ranked lists of recommendations: reduce tokens with no quality loss, and improve quality. It writes only its own output files. All output stays on your machine.

This plugin is Claude Code-specific: it reads the Claude Code transcript format (`~/.claude/projects/**/*.jsonl`).

Uses Claude Code-only frontmatter (`disable-model-invocation`, `argument-hint`), so it is not a portable Agent Skill.

## Install

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install usage-review@agentic-explorers
```

## Usage

The skill has `disable-model-invocation: true`, so Claude does not start it automatically. Run it yourself. Plugin skills are namespaced as `<plugin>:<skill>`:

```text
/usage-review:usage-review                                      # all available data
/usage-review:usage-review --start 2026-09-01 --end 2026-09-27  # date range, local time
/usage-review:usage-review --profile work=~/.claude-work        # add a second profile
```

The skill runs a fixed, tested script, `skills/usage-review/scripts/usage_report.py`, then Claude reads its output and writes two ranked lists of recommendations to `recommendations.md` in the output directory. Claude does not write or patch the script, and does not change the script's output files. You can also run the script yourself:

```text
python3 skills/usage-review/scripts/usage_report.py [--start YYYY-MM-DD] [--end YYYY-MM-DD]
    [--out DIR] [--profile NAME=PATH ...] [--changes PATH] [--prices PATH]
```

Output goes to `~/Documents/claude-usage/<today>/` (or `--out`):

| File | Content |
|---|---|
| `weekly_by_model_effort.csv`, `weekly_by_agent.csv` | Weekly tokens by model and effort, and by main vs subagent type, with subtotals, a grand total and cost-weighted columns |
| `diagnostics.json` | Peak context, compactions, usage-limit hits, repeated file reads, largest tool results, cache rewrites after idle gaps, sessions near the context limit, start-of-session context, builder/reviewer rounds and verdicts, before/after comparisons |
| `inventory.json` | Per profile: token-related `settings.json` keys, sizes of CLAUDE.md, memory files, skills and agent definitions, agent model/effort/tools, MCP server names |
| `findings.json` | Findings with the list (A: fewer tokens, no quality loss; B: better quality), measured value, threshold, evidence, estimated weekly tokens and cost weight, confidence, and score (cost weight x confidence) |
| `manifest.json` | Arguments, date range, files scanned, skipped lines, missing fields, unpriced models, raw vs deduplicated reconciliation, invariant results, sha256 of each output |
| `report.md` | The tables, diagnostics and findings in markdown |
| `recommendations.md` | Written by Claude, not by the script: the two ranked lists |

The script's outputs keep numbers, ids (session, agent run, tool use), tool names, model names and file paths: the paths of files that `Read` opened, the working directories of your sessions (as the paths of their `CLAUDE.md` files) and your config files. They never keep content: no prompts, answers, thinking, tool input other than the `Read` file path, or tool output. File paths can themselves be sensitive (project or client names); keep the output local.

Exit codes: 0 success; 2 input problem (bad argument or config file, no transcripts, or transcript format drift); 3 an internal check failed. For exit 3, the checks on the totals run before any file is written, so nothing is written; if the final manifest check fails, `manifest.json` is deleted and the other files stay, but without a manifest they are not a valid run.

Builder and reviewer subagent runs are grouped into tasks by their descriptions (words such as Build, Validate, Review, Fix and round markers are ignored), else by time order. A round is one reviewer run in a task. A finding whose runs were linked only by time is marked `inferred`.

Reviewer verdicts are read from the first line that starts with `VERDICT: APPROVE`, `VERDICT: PASS` or `VERDICT: CHANGES` (any case; `*`, `#` and spaces before it are allowed), in the reviewer's `SubagentHandback` message, else in its final text. PASS counts as approve. Anything else counts as `unclear`.

## Requirements

- Python 3.10 or later (standard library only). macOS, Linux or Windows.
- Claude Code transcripts in `~/.claude/projects/`. The script also reads `$CLAUDE_CONFIG_DIR` if it is set. Add other profiles (config directories) with `--profile NAME=PATH`.
- Optional: your own `changes.json`, to compare metrics before and after changes to your setup. The script uses `--changes PATH`, else `~/Documents/claude-usage/changes.json` (always this path, also with `--out`), else the empty template `skills/usage-review/scripts/changes.json`, which documents the format. The comparison needs at least 10 sessions on each side; with fewer it reports "insufficient data". The run is also compared with the latest earlier run in the parent directory of the output directory; only directories with a `manifest.json` from this script count, and the latest is chosen by the date range in that manifest.
- `skills/usage-review/scripts/prices.json`: per-model API list prices, used only as weights. Checked 2026-06-24, from the claude-api skill's cached price table. To update it, edit the `models` entries (USD per million tokens, and the cache-read multiplier) and the `checked` date, or pass your own file with `--prices`. A model with no matching id prefix is listed as unpriced and its cost cells stay empty; the script does not guess a price.
- Web access to `code.claude.com/docs`, so that Claude can check current setting defaults before it recommends them.

## Tested with

| Agent | Version | Model |
|---|---|---|
| Claude Code | 2.1.283 | not recorded |

The plugin manifest and the marketplace entry pass `claude plugin validate` on Claude Code 2.1.283.

The script's unit tests (`python -m unittest discover skills/usage-review/scripts/tests`) run in CI (`.github/workflows/plugin-tests.yml`) on Ubuntu, macOS and Windows with Python 3.10 and 3.13.

## Author

Sean Overby ([@soverby](https://github.com/soverby))
