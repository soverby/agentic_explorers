import { atom, read, update } from 'claude-code'
import type { EngineInterface, PluginOptions, Register } from 'claude-code'

import type { UsageMeterWindow } from '../types'

type Engine = Pick<EngineInterface, 'clock' | 'session' | 'state' | 'store' | 'ui'>

const windows = atom({ plugin: 'usage-meter', key: 'windows' } as const, [])
const nowMs = atom({ plugin: 'usage-meter', key: 'nowMs' } as const, 0)
const fired = atom({ plugin: 'usage-meter', key: 'fired' } as const, {})

const LABELS: Record<string, string> = {
  five_hour: '5h',
  seven_day: '7d',
  spend_limit: 'spend',
}
const BAR_MAX = 20
const BAR_MIN = 6
const LABEL_WIDTH = 5
const REFRESH_MS = 60_000

const label = (kind: string) => LABELS[kind] ?? kind.replace(/_/g, ' ')

// Reset instant in ms, or undefined when absent or not a date.
const resetMs = (w: UsageMeterWindow) => {
  const ms = w.resetsAt === undefined ? NaN : Date.parse(w.resetsAt)

  return Number.isNaN(ms) ? undefined : ms
}

// A window whose reset has passed holds a stale reading until the next response.
const isLive = (w: UsageMeterWindow, now: number) => {
  const at = resetMs(w)

  return at === undefined || at > now
}

const duration = (ms: number) => {
  const minutes = Math.floor(Math.max(0, ms) / 60_000)
  const days = Math.floor(minutes / 1440)
  const hours = Math.floor((minutes % 1440) / 60)

  if (days > 0) return `${days}d ${hours}h`
  if (hours > 0) return `${hours}h ${minutes % 60}m`
  if (minutes > 0) return `${minutes}m`

  return '<1m'
}

const countdown = (w: UsageMeterWindow, now: number) => {
  const at = resetMs(w)

  return at === undefined ? undefined : duration(at - now)
}

const pad2 = (n: number) => String(n).padStart(2, '0')

// Local wall-clock time with its UTC offset: 2026-10-08 14:30 (UTC+02:00).
const localTime = (ms: number) => {
  const d = new Date(ms)
  const offset = -d.getTimezoneOffset()
  const sign = offset < 0 ? '-' : '+'
  const abs = Math.abs(offset)

  return (
    `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ` +
    `${pad2(d.getHours())}:${pad2(d.getMinutes())} ` +
    `(UTC${sign}${pad2(Math.floor(abs / 60))}:${pad2(abs % 60)})`
  )
}

const shown = (percent: number) => `${Math.round(percent)}%`

// Label, gaps and percent are fixed; the bar and the reset text share the rest.
const fit = (columns: number, left: string | undefined) => {
  const room = LABEL_WIDTH + 1 + 4
  const options = left === undefined ? [''] : [`resets in ${left}`, left, '']

  for (const reset of options) {
    const bar = columns - room - 1 - (reset === '' ? 0 : reset.length + 2)

    if (bar >= BAR_MIN) return { bar: Math.min(BAR_MAX, bar), reset }
  }

  // No room for a bar: keep the short reset text if it fits.
  const reset = left !== undefined && room + left.length + 2 <= columns ? left : ''

  return { bar: 0, reset }
}

const levelOf = (percent: number, warnAt: number, critAt: number) =>
  percent >= critAt ? 2 : percent >= warnAt ? 1 : 0

const COLORS = ['success', 'warning', 'error'] as const

// Reset instants drift by seconds between readings; five-minute buckets keep one period one key.
const periodKey = (w: UsageMeterWindow) => {
  const at = resetMs(w)

  return `${w.kind}@${at === undefined ? '-' : Math.round(at / 300_000)}`
}

// The manifest bounds a percent to 1-100; a value outside it is pulled in, never fatal.
const readPercent = (options: PluginOptions, name: string, fallback: number) => {
  const value = options[name]

  return typeof value === 'number' && Number.isFinite(value)
    ? Math.min(100, Math.max(1, value))
    : fallback
}

const readDisplay = (options: PluginOptions) => (options.display === 'status' ? 'status' : 'band')

const sameWindows = (a: UsageMeterWindow[], b: UsageMeterWindow[]) =>
  a.length === b.length &&
  a.every((w, i) => {
    const o = b[i]

    return o !== undefined && w.kind === o.kind && w.resetsAt === o.resetsAt && w.percentUsed === o.percentUsed
  })

const isFiredMap = (value: unknown): value is Record<string, number> =>
  typeof value === 'object' &&
  value !== null &&
  !Array.isArray(value) &&
  Object.values(value).every(v => typeof v === 'number')

type Config = { display: 'band' | 'status'; warnAt: number; critAt: number }

let timer: { cancel: () => void } | null = null

const summary = (list: UsageMeterWindow[], now: number) =>
  list
    .filter(w => isLive(w, now))
    .map(w => {
      const left = countdown(w, now)

      return `${label(w.kind)} ${shown(w.percentUsed)}${left === undefined ? '' : ` (${left})`}`
    })
    .join(' · ')

const publish = ($: Engine, config: Config, list: UsageMeterWindow[], now: number) => {
  $.ui.status(config.display === 'status' ? summary(list, now) || undefined : undefined)
}

// Sends each toast once per window per reset period, remembered in state and the store.
const notify = async ($: Engine, config: Config, list: UsageMeterWindow[], now: number) => {
  const toasts: string[] = []
  const next = await update($, fired, old => {
    toasts.length = 0
    const kept: Record<string, number> = {}
    const current = new Set(list.map(periodKey))
    const kinds = new Set(list.map(w => w.kind))

    // Drop periods that have reset; keep kinds this reading does not mention.
    for (const [key, level] of Object.entries(old)) {
      if (current.has(key) || !kinds.has(key.split('@')[0] ?? '')) kept[key] = level
    }

    for (const w of list) {
      const key = periodKey(w)
      const level = levelOf(w.percentUsed, config.warnAt, config.critAt)
      const seen = kept[key] ?? 0

      // Without a reset time a drop below warn is the only sign of a new period.
      if (w.resetsAt === undefined && level === 0) delete kept[key]
      if (level <= seen) continue

      kept[key] = level
      const left = countdown(w, now)
      const reset = left === undefined ? '' : `, resets in ${left}`
      toasts.push(
        level === 2
          ? `${label(w.kind)} limit nearly used up: ${shown(w.percentUsed)}${reset}`
          : `${label(w.kind)} limit at ${shown(w.percentUsed)}${reset}`,
      )
    }

    return kept
  })

  if (toasts.length > 0) {
    await $.store.set('fired', next)
    for (const text of toasts) $.ui.toast(text, { timeoutMs: 8000 })
  }
}

// The countdown text only moves with the clock; refresh it once a minute while windows exist.
const ensureTimer = ($: Engine, config: Config, hasWindows: boolean) => {
  if (!hasWindows) {
    timer?.cancel()
    timer = null

    return
  }

  if (timer !== null) return

  timer = $.clock.every(REFRESH_MS, () => {
    void (async () => {
      const now = await $.clock.now()
      const list = await read($, windows)
      await update($, nowMs, () => now)
      publish($, config, list, now)
    })().catch(() => {})
  })
}

const apply = async ($: Engine, config: Config, list: UsageMeterWindow[]) => {
  const now = await $.clock.now()
  const copy = list.map(w => ({ ...w }))
  await update($, windows, () => copy)
  await update($, nowMs, () => now)
  publish($, config, copy, now)
  ensureTimer($, config, copy.length > 0)
  await notify($, config, copy, now)
}

export const register: Register = (on, options) => {
  const warn = readPercent(options, 'warnAt', 80)
  // A critical level under the warning level would never be told apart; raise it.
  const config: Config = {
    display: readDisplay(options),
    warnAt: warn,
    critAt: Math.max(warn, readPercent(options, 'critAt', 95)),
  }
  const { display, warnAt, critAt } = config

  on('session.start', async ($, e, next) => {
    // Separate tries: one failing must not skip the other. Observe only.
    try {
      await $.command.register({
        name: 'limits',
        description: 'Show account rate-limit windows with exact percent and reset time',
      })
    } catch {
      // The meter still works without its command.
    }

    try {
      const stored = await $.store.get('fired')
      const known = await read($, fired)

      if (isFiredMap(stored) && Object.keys(known).length === 0) {
        await update($, fired, () => stored)
      }

      await apply($, config, (await $.session.usage()).rateLimits)
    } catch {
      // A failed reading leaves the session as it was.
    }

    return next(e)
  })

  on('session.measure', async ($, e, next) => {
    try {
      // `changed` can miss a window that only rolled over (same percent, new reset time).
      if (!sameWindows(await read($, windows), e.rateLimits)) {
        await apply($, config, e.rateLimits)
      }
    } catch {
      // Observe only.
    }

    return next(e)
  })

  on('command.run', { command: 'limits' }, async $ => {
    const now = await $.clock.now()
    const list = (await $.session.usage()).rateLimits

    if (list.length === 0) {
      return {
        text: 'No rate-limit windows reported (not a subscription plan, or no response yet).',
      }
    }

    const lines = list.map(w => {
      const at = resetMs(w)
      const resets =
        at === undefined
          ? 'reset time unknown'
          : at <= now
            ? `reset at ${localTime(at)}, reading is stale until the next response`
            : `resets ${localTime(at)}, in ${duration(at - now)}`

      return `${label(w.kind)} (${w.kind}): ${w.percentUsed}% used, ${resets}`
    })

    return { text: lines.join('\n') }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const list = await read($, windows)
    const now = await read($, nowMs)
    const live = list.filter(w => isLive(w, now))

    if (display === 'status' || e.props.hasSurvey || live.length === 0) {
      return next(e)
    }

    // Hooks beneath (another mod's band) draw too; ours goes under theirs.
    // No width on this Box: the engine refuses an engine node under a Box with one.
    const below = await next(e)
    const { Box, Text } = $.ui.resolve(e)
    const columns = Math.max(1, e.props.bodyColumns)

    return (
      <Box flexDirection="column">
        {below}
        {live.map(w => {
          const color = COLORS[levelOf(w.percentUsed, warnAt, critAt)]
          const percent = shown(w.percentUsed).padStart(4)
          const left = countdown(w, now)
          const { bar, reset } = fit(columns, left)
          const filled = Math.round((Math.min(100, Math.max(0, w.percentUsed)) / 100) * bar)

          return (
            <Box key={periodKey(w)} width={columns} overflow="hidden">
              <Text dimColor>{label(w.kind).padEnd(LABEL_WIDTH)} </Text>
              {bar > 0 && (
                <Text color={color}>
                  {'█'.repeat(filled)}
                  {'░'.repeat(bar - filled)}{' '}
                </Text>
              )}
              <Text color={color}>{percent}</Text>
              {reset !== '' && <Text dimColor>{`  ${reset}`}</Text>}
            </Box>
          )
        })}
      </Box>
    )
  })
}
