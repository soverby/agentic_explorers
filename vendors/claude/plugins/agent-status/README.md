# agent-status

Live sub-agent status for Claude Code.

## Install

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install agent-status@agentic-explorers
```

This is a Claude Code mod (a plugin of function hooks). Its code and tests are in `hooks/`.

## What it shows

- **Band above the prompt.** One row per active agent (pending, running or
  waiting) plus agents that ended in the last 10 s. Each row: status glyph,
  agent type, description, current tool, elapsed time, tokens (`1.2k`, `12k`,
  `1.2M`). Children are indented under their parent. At most 6 rows, then
  `+N more`. The band draws above any other AbovePrompt band (it calls `next` and keeps what comes back). It is absent when no agent is active or recently ended, and
  it yields to a survey. Sized to the band's width; works at 60 columns.
- **`/agent-status` pane.** Every agent of the session, ended ones included:
  type, description, status, duration, tool count, tokens and parent.
  (`/agents` is a Claude Code built-in; a plugin cannot register it.)
- **Toast** when an agent completes, fails or is killed: one per agent.

Tokens are `input + output + cache-write` summed over the agent's turns
(`turn.complete` with that `agentId`). Cache reads re-count the same context at
every step, so they are tracked but left out of the shown total.

## Options (`userConfig`)

| Option  | Values                 | Default | Effect |
| ------- | ---------------------- | ------- | ------ |
| `style` | `plain`, `critters`    | `plain` | `critters` swaps the status glyph of a live agent for a small ASCII creature whose frame follows the clock (500 ms). Ended agents keep their glyph. |

## How it works

`agent.spawn`, `tool.call` (with `agentId`) and `turn.complete` (with
`agentId`) are observed and always passed on unchanged. `$.agent.list()` is the
authority for status, type, description and parent. While any agent is active a
`$.clock.every` timer (2 s; 500 ms in critters mode, list still read every 2 s)
refreshes the list; it stops when none is active, and one `$.clock.after`
redraws once the last ended row passes its 10 s. Everything drawn lives in
`$.state` (`agent-status.agents`, `agent-status.now`), declared in
`types/index.d.ts`.

## Permissions used

None outside `$.agent.list`, `$.clock`, `$.command.register`, `$.state`,
`$.ui` (`resolve`, `toast`, `open`, `log` to the debug log). No `$.fs`,
`$.process`, `$.http`, `$.env`, `$.model`, `$.session.authorize`, no transcript
reads, no network. Check with `claude plugin validate <dir>`.

## Agents the list never names

Engine forks (compaction, memory) and workflow agents carry agent ids that
`$.agent.list()` never lists. Tool calls and turns of such an id are ignored
(they never create a row), and a spawned id the list never names is dropped
after 4 refreshes, so neither can keep the timer running. An agent that was
listed and then vanishes from the list is shown as `ended`.

## Not done

- The `Spinner` `message` is not rewritten while agents run: it would change an
  event's props, and this mod is observe-only.
- An agent that is resumed after it ended does not toast again (one toast per
  agent).

## Develop

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
