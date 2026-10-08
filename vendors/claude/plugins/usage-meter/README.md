# usage-meter

Shows your account rate-limit windows (subscription plans): `5h`, `7d` and `spend`.

## Install

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install usage-meter@agentic-explorers
```

This is a Claude Code mod (a plugin of function hooks). Its code and tests are in `hooks/`.

## What it shows

- **Band** above the prompt, one line per window: label, bar, percent, `resets in 2h 14m`. The bar is green below `warnAt`, yellow from `warnAt`, red from `critAt`. The line is sized to the band width and drops the bar, then the reset text, on very narrow terminals (works at 60 columns).
- **Hidden** when the account reports no windows (API key, no subscription, or before the first response), and while a survey holds the band.
- **Stale readings** are not drawn: once a window's reset time has passed, its row stays hidden until the next response reports a fresh reading.
- **Toasts** once per window per reset period when usage reaches `warnAt`, and once more at `critAt`. A jump straight past `critAt` gives one toast. Crossings are kept per `kind@resetsAt` in `$.state` (survives hot reload) and in `$.store` (survives a new session), so a reload does not toast again.
- **Countdown text** refreshes every 60 s while windows exist; the timer stops when none do.

## Command

`/limits` prints each window with its exact percent, the reset time in local time (with UTC offset) and the time left:

```
5h (five_hour): 82% used, resets 2026-10-08 14:14 (UTC+00:00), in 2h 14m
7d (seven_day): 41.5% used, resets 2026-10-11 16:00 (UTC+00:00), in 3d 4h
```

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `display` | `band` | `band` draws above the prompt. `status` puts a one-line summary in `$.ui.status` (`5h 82% (2h 14m) · 7d 42% (3d 4h)`) and draws no band. |
| `warnAt` | `80` | Percent for the warning toast and the yellow color. |
| `critAt` | `95` | Percent for the critical toast and the red color. Values are held to 1-100, and `critAt` below `warnAt` is raised to `warnAt`. |

## Permissions

None outside `$.session.usage`, `$.ui` (`resolve`, `toast`, `status`), `$.state`, `$.store`, `$.clock` (`now`, `every`) and `$.command.register`. No `$.fs`, `$.process`, `$.http`, `$.env`, `$.model`, `$.session.authorize`, transcript or credential access, and no network. Every hook except the `/limits` handler is observe-only: it calls `next(e)` and passes the result on. The band hook adds its rows under whatever the hooks beneath it draw (for example another mod's band) and never replaces it.

## Notes

- Unknown window kinds are labelled with their raw `kind` (underscores as spaces).
- Data comes from `$.session.usage().rateLimits` at session start and from `session.measure` pushes. The engine pushes when a window moves a whole point, so a toast can lag by up to one point.
- `session.start` fires again on each fresh load of the mod, a hot reload included, so the 60 s timer, the command and the first reading come back at once.
- Each `session.measure` is compared with the last reading (kind, reset time, percent), not only the engine's `changed` list, so a window that only rolled over (new reset time, same percent) updates the band.
- Toast periods are keyed by window kind and the reset time rounded to five minutes, so a reset time that drifts by seconds does not toast twice.

## Tested with

| Agent | Version | Model |
|---|---|---|
| Claude Code | 2.1.294 | n/a (the mod makes no model calls) |

## Author

Sean Overby ([@soverby](https://github.com/soverby))
