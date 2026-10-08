# context-meter

Token counts and context fill, shown without spending a token.

## Install

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install context-meter@agentic-explorers
```

This is a Claude Code mod (a plugin of function hooks). Its code and tests are in `hooks/`.

## What it shows

A one-line status under the prompt (`$.ui.status`):

```
ctx 42% 84k/200k · turn in 12.3k out 1.1k cache 90% · $1.23
```

- `ctx`: the live window. Percent and tokens are from the last API response; `-` until the first response after a start or a compaction.
- `turn`: the last main-loop turn. `in` is all input tokens (uncached + cache write + cache read), `out` the output, `cache` the cache-read share of `in`. A turn with several tool steps sums its requests. Shown after the first main-loop turn. Subagent turns are ignored.
- cost: the session total in US dollars, rounded to whole cents for display.

The line is refreshed on `session.start`, `turn.start`, `session.measure` and main-loop `turn.complete`. `/clear` resets the turn figures and removes the line.

A toast fires once when the fill reaches `warnAt` percent. It re-arms when the fill drops below `warnAt` (a compaction, or `/clear`).

## Command

`/ctx` prints a markdown table of the context categories (tokens and share of the window, from `$.session.usage({ breakdown: "summary" })`, a local estimate that costs nothing), the total, the session cost, and the summed main-loop token totals for this session (input, output, cache read, cache write, turns).

## Options

| Option   | Type   | Default | Meaning                                   |
| -------- | ------ | ------- | ----------------------------------------- |
| `warnAt` | number | 80      | Context percent (1 to 100) for the toast. |

## Status line and a configured `statusLine`

`$.ui.status` is documented as "this plugin's status line under the prompt, beside the engine's own pinned notices", one per plugin. The declaration file does not mention the user's `statusLine` setting. So nothing says this line replaces or merges with a configured status line command; it is a separate, plugin-owned line.

## Permissions

Only `$.session.usage`, `$.state`, `$.ui.status`, `$.ui.toast`, `$.command.register`, and the events `session.start`, `turn.start`, `session.measure`, `session.end`, `turn.complete` and `command.run`. No filesystem, process, network, environment, credentials, or model calls. Every hook except the `/ctx` handler passes `next(e)` unchanged and has a `.catch` that continues the chain.

## State

Declared in `types/index.d.ts` under `context-meter`: `lastTurn`, `totals`, `isWarned`. They survive a hot reload. The status line text itself is rebuilt on the next event after a reload.

## Development

```
claude plugin validate <dir>
claude plugin test <dir>
```

## Tested with

| Agent | Version | Model |
|---|---|---|
| Claude Code | 2.1.294 | n/a (the mod makes no model calls) |

## Author

Sean Overby ([@soverby](https://github.com/soverby))
