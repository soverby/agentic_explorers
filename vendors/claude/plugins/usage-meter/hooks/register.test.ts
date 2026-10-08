import { expect, mock, test } from 'claude-code/testing'
import type { On } from 'claude-code'

const SURFACES = ['terminal', 'desktop'] as const
const T0 = Date.parse('2026-10-08T12:00:00Z')
const PLUGIN = 'usage-meter'

type Limit = { kind: string; percentUsed: number; resetsAt?: string }

const FIVE_HOUR: Limit = { kind: 'five_hour', percentUsed: 82, resetsAt: '2026-10-08T14:14:00Z' }
const SEVEN_DAY: Limit = { kind: 'seven_day', percentUsed: 41.5, resetsAt: '2026-10-11T16:00:00Z' }

// Answers what the engine would beneath the plugin and records what it was told to show.
const world = (
  on: On,
  rateLimits: Limit[],
  stored?: Record<string, unknown>,
  refuseCommand = false,
) => {
  const clock = mock.clock(on, { now: T0 })
  mock.store(on, stored)
  const seen = { toasts: [] as string[], statuses: [] as (string | undefined)[], limits: rateLimits }

  // The engine's own band, beneath every plugin.
  on('ui.render', { component: 'AbovePrompt' }, () => ({ type: 'engine', ref: 1 }) as never)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.measure', (_$, e) => ({ changed: e.changed }))
  on('session.usage', () => ({
    value: { startedAt: T0, context: { window: 200_000 }, rateLimits: seen.limits },
  }))
  on('command.register', (_$, e) =>
    refuseCommand ? { deny: 'refused' } : { value: { command: e.name } },
  )
  on('ui.toast', (_$, e) => {
    seen.toasts.push(e.text)

    return { value: undefined }
  })
  on('ui.status', (_$, e) => {
    seen.statuses.push(e.text)

    return { value: undefined }
  })

  return { clock, seen }
}

const START = { cwd: '/work', surface: 'terminal', isInteractive: true } as const

const bandProps = (bodyColumns: number) => ({
  hasSurvey: false,
  isWorking: false,
  maxRows: 10,
  bodyColumns,
  scroll: { offset: 0, bodyRows: 10 },
  view: {},
})

const measure = (limits: Limit[]) => ({
  context: { window: 200_000 },
  rateLimits: limits,
  changed: ['rateLimits' as const],
})

// Plain text of every row of the drawn band, one string per row.
const rows = (node: unknown): string[] => {
  const flat = (n: unknown): string =>
    typeof n === 'string'
      ? n
      : Array.isArray(n)
        ? n.map(flat).join('')
        : n && typeof n === 'object' && 'children' in n
          ? flat((n as { children: unknown }).children)
          : ''
  const top = node as { children?: unknown[] }

  // Our rows have a bar or percent; the node drawn beneath is skipped.
  return (top.children ?? []).map(flat).filter(line => /\d%/.test(line))
}

test('band shows one line per window on terminal and desktop', async ($, on) => {
  world(on, [FIVE_HOUR, SEVEN_DAY])
  await $.session.start(START)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: 'AbovePrompt',
      props: bandProps(60),
    })
    const lines = rows(await ui.drawn())

    expect(lines).toHaveLength(2)
    expect(lines[0]).toMatch(/^5h\s+█+░+\s+82%\s+resets in 2h 14m$/)
    expect(lines[1]).toMatch(/^7d\s+█+░+\s+42%\s+resets in 3d 4h$/)
    await ui.unmount()
  }
})

test('band fits 60 columns and narrower', async ($, on) => {
  world(on, [FIVE_HOUR, { kind: 'spend_limit', percentUsed: 100.4, resetsAt: '2026-10-31T00:00:00Z' }])
  await $.session.start(START)

  for (const surface of SURFACES) {
    for (const columns of [60, 40, 24, 16, 12]) {
      const ui = await $.ui.mount({
        plugin: PLUGIN,
        surface,
        component: 'AbovePrompt',
        props: bandProps(columns),
      })

      for (const line of rows(await ui.drawn())) {
        expect(line.length <= columns, `${columns} columns: ${line}`).toBe(true)
      }
      await ui.unmount()
    }
  }
})

test('band colors follow warn and critical levels', async ($, on) => {
  world(on, [
    { ...FIVE_HOUR, percentUsed: 30 },
    { ...SEVEN_DAY, percentUsed: 80 },
    { kind: 'spend_limit', percentUsed: 95 },
  ])
  await $.session.start(START)
  const ui = await $.ui.mount({
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: bandProps(60),
  })
  const colors = (await ui.findAll({ type: 'Text' }))
    .map(t => t.props.color)
    .filter(c => c !== undefined)

  expect(colors).toEqual(['success', 'success', 'warning', 'warning', 'error', 'error'])
})

test('band is hidden with an empty rateLimits on terminal and desktop', async ($, on) => {
  world(on, [])
  await $.session.start(START)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: 'AbovePrompt',
      props: bandProps(60),
    })

    expect(await ui.findAll({ type: 'Text' })).toHaveLength(0)
    expect((await ui.drawn()).type).toBe('engine')
    await ui.unmount()
  }
})

test('toasts fire once per window per reset period at warn and critical', async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 50 }])
  await $.session.start(START)
  expect(seen.toasts).toEqual([])

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 79 }]))
  expect(seen.toasts).toEqual([])

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 80 }]))
  expect(seen.toasts).toEqual(['5h limit at 80%, resets in 2h 14m'])

  // Same period again: nothing more at warn level.
  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 88 }]))
  expect(seen.toasts).toHaveLength(1)

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 95 }]))
  expect(seen.toasts).toHaveLength(2)
  expect(seen.toasts[1]).toBe('5h limit nearly used up: 95%, resets in 2h 14m')

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 99 }]))
  expect(seen.toasts).toHaveLength(2)

  // The 7d window has its own period.
  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 99 }, { ...SEVEN_DAY, percentUsed: 81 }]))
  expect(seen.toasts).toHaveLength(3)
  expect(seen.toasts[2]).toMatch(/^7d limit at 81%/)

  // A new reset period of the 5h window toasts again.
  const next = { ...FIVE_HOUR, percentUsed: 85, resetsAt: '2026-10-08T19:14:00Z' }
  await $.session.measure(measure([next, { ...SEVEN_DAY, percentUsed: 81 }]))
  expect(seen.toasts).toHaveLength(4)
  expect(seen.toasts[3]).toMatch(/^5h limit at 85%/)
})

test('a jump straight to critical toasts once, not warn then critical', async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 10 }])
  await $.session.start(START)
  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 97 }]))
  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 98 }]))
  expect(seen.toasts).toEqual(['5h limit nearly used up: 97%, resets in 2h 14m'])
})

test('toasts do not repeat after a reload (state kept, hooks run again)', async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 96 }])
  await $.session.start(START)
  expect(seen.toasts).toHaveLength(1)

  // A reload runs register and session.start again with $.state intact.
  await $.session.start(START)
  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 96 }]))
  expect(seen.toasts).toHaveLength(1)
})

test('toasts do not repeat in a new session (store kept, state empty)', async ($, on) => {
  const key = Math.round(Date.parse('2026-10-08T14:14:00Z') / 300_000)
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 85 }], { fired: { [`five_hour@${key}`]: 1 } })
  await $.session.start(START)
  expect(seen.toasts).toEqual([])

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 96 }]))
  expect(seen.toasts).toHaveLength(1)
})

test('warnAt and critAt options move the thresholds', { options: { warnAt: 50, critAt: 60 } }, async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 55 }])
  await $.session.start(START)
  expect(seen.toasts).toEqual(['5h limit at 55%, resets in 2h 14m'])

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 61 }]))
  expect(seen.toasts[1]).toBe('5h limit nearly used up: 61%, resets in 2h 14m')
})

test('status mode puts a summary in $.ui.status and draws no band', { options: { display: 'status' } }, async ($, on) => {
  const { seen } = world(on, [FIVE_HOUR, SEVEN_DAY])
  await $.session.start(START)

  expect(seen.statuses.at(-1)).toBe('5h 82% (2h 14m) · 7d 42% (3d 4h)')

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: 'AbovePrompt',
      props: bandProps(60),
    })

    expect((await ui.drawn()).type).toBe('engine')
    await ui.unmount()
  }

  await $.session.measure(measure([]))
  expect(seen.statuses.at(-1)).toBeUndefined()
})

test('band mode leaves $.ui.status clear', async ($, on) => {
  const { seen } = world(on, [FIVE_HOUR])
  await $.session.start(START)
  expect(seen.statuses.every(text => text === undefined)).toBe(true)
})

test('countdown refreshes every 60 s while windows exist and stops after', async ($, on) => {
  const { clock, seen } = world(on, [FIVE_HOUR])
  await $.session.start(START)
  const ui = await $.ui.mount({
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: bandProps(60),
  })
  expect(rows(await ui.drawn())[0]).toMatch(/resets in 2h 14m$/)

  await clock.advance(60_000)
  expect(rows(await ui.drawn())[0]).toMatch(/resets in 2h 13m$/)

  // Past the reset the stale reading is not drawn.
  await clock.advance(3 * 3_600_000)
  expect((await ui.drawn()).type).toBe('engine')

  await $.session.measure(measure([]))
  const before = seen.statuses.length
  await clock.advance(10 * 60_000)
  expect(seen.statuses).toHaveLength(before)
})

test('/limits lists exact percent with local time and relative reset', async ($, on) => {
  world(on, [FIVE_HOUR, SEVEN_DAY])
  await $.session.start(START)

  const { text } = await $.command.run({
    command: 'limits',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })
  const lines = (text ?? '').split('\n')

  expect(lines).toHaveLength(2)
  expect(lines[0]).toMatch(
    /^5h \(five_hour\): 82% used, resets \d{4}-\d{2}-\d{2} \d{2}:\d{2} \(UTC[+-]\d{2}:\d{2}\), in 2h 14m$/,
  )
  expect(lines[1]).toMatch(/^7d \(seven_day\): 41\.5% used, resets .*, in 3d 4h$/)
})

test('/limits says so when no window is reported', async ($, on) => {
  world(on, [])
  await $.session.start(START)

  const { text } = await $.command.run({
    command: 'limits',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: false, columns: 80 },
  })

  expect(text).toMatch(/^No rate-limit windows reported/)
})

const BENEATH = {
  name: 'band-beneath',
  tier: 'append',
  register(on: On) {
    on('ui.render', { component: 'AbovePrompt' }, () => {
      return { type: 'Text', props: {}, children: ['agent row beneath'] } as never
    })
  },
} as const

test('hooks beneath the band still run and draw, on terminal and desktop', { plugins: [BENEATH] }, async ($, on) => {
  const clock = mock.clock(on, { now: T0 })
  void clock
  mock.store(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.toast', () => ({ value: undefined }))
  on('ui.status', () => ({ value: undefined }))
  on('session.usage', () => ({
    value: { startedAt: T0, context: { window: 200_000 }, rateLimits: [FIVE_HOUR] },
  }))
  await $.session.start(START)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({
      plugin: PLUGIN,
      surface,
      component: 'AbovePrompt',
      props: bandProps(60),
    })

    expect(await ui.find({ type: 'Text', text: /agent row beneath/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /82%/ })).toBeDefined()
    expect(rows(await ui.drawn())).toHaveLength(1)
    await ui.unmount()
  }
})

test('hooks beneath still draw in status mode', { plugins: [BENEATH], options: { display: 'status' } }, async ($, on) => {
  mock.clock(on, { now: T0 })
  mock.store(on)
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('command.register', (_$, e) => ({ value: { command: e.name } }))
  on('ui.status', () => ({ value: undefined }))
  on('session.usage', () => ({
    value: { startedAt: T0, context: { window: 200_000 }, rateLimits: [FIVE_HOUR] },
  }))
  await $.session.start(START)
  const ui = await $.ui.mount({
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: bandProps(60),
  })

  expect(await ui.find({ type: 'Text', text: /agent row beneath/ })).toBeDefined()
})

test('a window that only rolled over (same percent, new reset time) updates the band and toasts', async ($, on) => {
  const first = { kind: 'five_hour', percentUsed: 85, resetsAt: '2026-10-08T13:00:00Z' }
  const { seen } = world(on, [first])
  await $.session.start(START)
  expect(seen.toasts).toHaveLength(1)

  const ui = await $.ui.mount({
    plugin: PLUGIN,
    surface: 'terminal',
    component: 'AbovePrompt',
    props: bandProps(60),
  })
  expect(rows(await ui.drawn())[0]).toMatch(/resets in 1h 0m$/)

  // `changed` names only cost: the old gate would have ignored this.
  const rolled = { ...first, resetsAt: '2026-10-08T18:00:00Z' }
  await $.session.measure({ context: { window: 200_000 }, rateLimits: [rolled], changed: ['cost'] })
  expect(rows(await ui.drawn())[0]).toMatch(/resets in 6h 0m$/)
  expect(seen.toasts).toHaveLength(2)
})

test('a reset time that drifts across a minute does not toast again; a new period does', async ($, on) => {
  const { seen } = world(on, [{ kind: 'five_hour', percentUsed: 85, resetsAt: '2026-10-08T14:14:59Z' }])
  await $.session.start(START)
  expect(seen.toasts).toHaveLength(1)

  const drifted = { kind: 'five_hour', percentUsed: 86, resetsAt: '2026-10-08T14:15:01Z' }
  await $.session.measure(measure([drifted]))
  expect(seen.toasts).toHaveLength(1)

  await $.session.measure(measure([{ ...drifted, resetsAt: '2026-10-08T19:15:01Z' }]))
  expect(seen.toasts).toHaveLength(2)
})

test('critAt below warnAt is raised to warnAt', { options: { warnAt: 90, critAt: 70 } }, async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 85 }])
  await $.session.start(START)
  // 85 is past critAt as typed (70) but under the warning level: no toast.
  expect(seen.toasts).toEqual([])

  await $.session.measure(measure([{ ...FIVE_HOUR, percentUsed: 91 }]))
  // At 91 the raised critical level (90) applies: one critical toast, no warn toast first.
  expect(seen.toasts).toEqual(['5h limit nearly used up: 91%, resets in 2h 14m'])
})

test('a failing command registration does not stop the first reading', async ($, on) => {
  const { seen } = world(on, [{ ...FIVE_HOUR, percentUsed: 96 }], undefined, true)
  await $.session.start(START)
  expect(seen.toasts).toHaveLength(1)
})
