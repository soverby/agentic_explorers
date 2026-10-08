import { expect, test } from 'claude-code/testing'
import type { On } from 'claude-code'
import type { SessionContextBreakdown, SessionUsage, SessionUsageArgs } from 'claude-code'

import { kfmt, usd } from './format'

const usageOf = (tokens: number | undefined, window = 200_000, usdSpent = 1.234): SessionUsage => ({
  startedAt: 0,
  context: {
    tokens,
    window,
    percent: tokens === undefined ? undefined : Math.floor((tokens * 100) / window),
  },
  rateLimits: [],
  cost: { usd: usdSpent },
})

const breakdown = {
  categories: [
    { name: 'System prompt', tokens: 3200, color: 'a', isDeferred: false, kind: 'used' },
    { name: 'Messages', tokens: 50_000, color: 'b', isDeferred: false, kind: 'used' },
    { name: 'MCP | tools', tokens: 900, color: 'c', isDeferred: true, kind: 'deferred' },
    { name: 'Free space', tokens: 146_800, color: 'd', isDeferred: false, kind: 'free' },
  ],
  totalTokens: 53_200,
  maxTokens: 160_000,
  rawMaxTokens: 160_000, // a compaction window smaller than the model's 200k
  autocompactSource: 'settings',
  percentage: 27,
  gridRows: [],
  model: 'test-model',
  memoryFiles: [],
  mcpTools: [],
  agents: [],
  isAutoCompactEnabled: true,
  apiUsage: null,
} as unknown as SessionContextBreakdown

// The engine side: what $.session.usage answers, and what the plugin shows.
const world = (
  on: On,
  start: SessionUsage,
  { isBroken = false, isRegisterRefused = false } = {},
) => {
  const seen = { usage: start, args: [] as (SessionUsageArgs | undefined)[] }
  const statuses: (string | undefined)[] = []
  const toasts: string[] = []
  const registered: string[] = []

  on('session.usage', (_$, e) => {
    if (isBroken) throw new Error('boom')
    seen.args.push(e)
    const value =
      e.breakdown === undefined
        ? seen.usage
        : { ...seen.usage, context: { ...seen.usage.context, breakdown } }
    return { value }
  })
  on('ui.status', (_$, e) => {
    statuses.push(e.text)
    return { value: undefined }
  })
  on('ui.toast', (_$, e) => {
    toasts.push(e.text)
    return { value: undefined }
  })
  on('command.register', (_$, e) => {
    if (isRegisterRefused) return { deny: 'refused' }
    registered.push(e.name)
    return { value: { command: e.name } }
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('session.measure', (_$, e) => ({ changed: e.changed }))
  on('session.end', (_$, e) => ({ sessionId: e.sessionId }))
  on('turn.complete', () => ({ text: '' }))
  on('turn.start', (_$, e) => ({ turnId: e.turnId }))

  return { seen, statuses, toasts, registered, last: () => statuses[statuses.length - 1] }
}

const measure = (usage: SessionUsage) => ({
  context: usage.context,
  rateLimits: usage.rateLimits,
  cost: usage.cost,
  changed: ['context' as const],
})

const turnOf = (usage: Record<string, number>, agentId?: string) => ({
  answer: 'ok',
  durationMs: 10,
  isAborted: false,
  turnId: 't1',
  reason: 'answer' as const,
  ...(agentId === undefined ? {} : { agentId }),
  usage: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_input_tokens: 0,
    cache_creation_input_tokens: 0,
    model: 'm',
    ...usage,
  },
})

const ctxCommand = {
  command: 'ctx',
  args: '',
  origin: { kind: 'composer' as const },
  presentation: { isFullscreen: false, columns: 80 },
}

test('format helpers', () => {
  expect(kfmt(950)).toBe('950')
  expect(kfmt(12_300)).toBe('12.3k')
  expect(kfmt(84_000)).toBe('84k')
  expect(kfmt(200_000)).toBe('200k')
  expect(kfmt(999_400)).toBe('999k')
  expect(kfmt(999_600)).toBe('1.0M')
  expect(kfmt(1_250_000)).toBe('1.3M')
  expect(usd(1.234)).toBe('$1.23')
  expect(usd(0.05)).toBe('$0.05')
})

test('a measure event sets the status text', async ($, on) => {
  const w = world(on, usageOf(undefined))
  await $.session.start({ cwd: '/', surface: null, isInteractive: true })
  expect(w.last()).toBe('ctx -/200k · $1.23')

  const next = usageOf(84_000)
  w.seen.usage = next
  await $.session.measure(measure(next))

  expect(w.last()).toBe('ctx 42% 84k/200k · $1.23')
  expect((w.last() ?? '').length).toBeLessThanOrEqual(60)
})

test('main-loop turn.complete shows last-turn figures; a subagent turn is ignored', async ($, on) => {
  const w = world(on, usageOf(84_000))

  await $.turn.complete(
    turnOf({ input_tokens: 1000, output_tokens: 1100, cache_read_input_tokens: 9000, cache_creation_input_tokens: 2300 }),
  )
  expect(w.last()).toBe('ctx 42% 84k/200k · turn in 12.3k out 1.1k cache 73% · $1.23')
  expect((w.last() ?? '').length).toBeLessThanOrEqual(60)

  const before = w.statuses.length
  await $.turn.complete(turnOf({ input_tokens: 500_000, output_tokens: 400_000 }, 'agent-1'))
  expect(w.statuses.length).toBe(before)
  expect(w.last()).toBe('ctx 42% 84k/200k · turn in 12.3k out 1.1k cache 73% · $1.23')

  const ctx = await $.command.run(ctxCommand)
  expect(ctx.text).toContain('(1 turns)')
  expect(ctx.text).toContain('| 1,000 | 1,100 | 9,000 | 2,300 |')
})

test('warn toast fires once and re-arms after the fill drops', async ($, on) => {
  const w = world(on, usageOf(10_000))
  const go = async (tokens: number | undefined) => {
    const u = usageOf(tokens)
    w.seen.usage = u
    await $.session.measure(measure(u))
  }

  await go(100_000)
  expect(w.toasts).toHaveLength(0)
  await go(170_000)
  expect(w.toasts).toHaveLength(1)
  expect(w.toasts[0]).toContain('85%')
  await go(180_000)
  await go(190_000)
  expect(w.toasts).toHaveLength(1)

  await go(undefined) // compacted: no reading until the next response
  await go(30_000)
  await go(165_000)
  expect(w.toasts).toHaveLength(2)
})

test('warnAt comes from the option', { options: { warnAt: 50 } }, async ($, on) => {
  const w = world(on, usageOf(10_000))
  const u = usageOf(110_000)
  w.seen.usage = u
  await $.session.measure(measure(u))
  expect(w.toasts).toHaveLength(1)
})

test('/ctx answers a markdown table with totals, cost and token sums', async ($, on) => {
  const w = world(on, usageOf(84_000, 200_000, 2.5))
  const { text = '' } = await $.command.run(ctxCommand)

  expect(w.seen.args.some(a => a?.breakdown === 'summary')).toBe(true)
  expect(text).toContain('| Category | Tokens | % of window |')
  expect(text).toContain('| --- | ---: | ---: |')
  expect(text).toContain('| System prompt | 3,200 | 2.0% |')
  expect(text).toContain('| Messages | 50,000 | 31.3% |')
  expect(text).toContain('| MCP \\| tools (deferred) | 900 | - |')
  expect(text).toContain('| Free space | 146,800 | 91.8% |')
  expect(text).toContain('| **Total used** | **53,200** | **33.3%** |')
  expect(text).toContain('**Session cost** $2.50')
  expect(text).toContain('84,000 of 200,000 tokens (42%)')
  expect(text).toContain('Compaction window 160,000 tokens (model window 200,000)')
  expect(text).toContain('(0 turns)')
})

test('/clear resets the figures and clears the status', async ($, on) => {
  const w = world(on, usageOf(84_000))
  await $.turn.complete(turnOf({ input_tokens: 10, output_tokens: 20 }))
  await $.session.end({ reason: 'clear', sessionId: 's', resume: { id: 's' } })
  expect(w.last()).toBeUndefined()

  const { text = '' } = await $.command.run(ctxCommand)
  expect(text).toContain('(0 turns)')
})

test('a failing usage read does not break the chain or skip /ctx registration', async ($, on) => {
  const w = world(on, usageOf(1000), { isBroken: true })

  const started = await $.session.start({ cwd: '/', surface: null, isInteractive: true })
  expect(started.cwd).toBe('/')
  expect(w.registered).toEqual(['ctx'])

  const done = await $.turn.complete(turnOf({ input_tokens: 5, output_tokens: 6 }))
  expect(done.text).toBe('')

  const u = usageOf(2000)
  const measured = await $.session.measure(measure(u))
  expect(measured.changed).toEqual(['context'])
  expect(w.last()).toBe('ctx 1% 2k/200k · turn in 5 out 6 cache 0% · $1.23')
})

test('the first status line shows even when /ctx registration is refused', async ($, on) => {
  const w = world(on, usageOf(84_000), { isRegisterRefused: true })
  const started = await $.session.start({ cwd: '/', surface: null, isInteractive: true })

  expect(started.cwd).toBe('/')
  expect(w.last()).toBe('ctx 42% 84k/200k · $1.23')
})

test('a turn start puts the line back (no session.start after /clear)', async ($, on) => {
  const w = world(on, usageOf(84_000))
  expect(w.statuses).toHaveLength(0)

  await $.turn.start({ text: 'hi', turnId: 't1' })
  expect(w.last()).toBe('ctx 42% 84k/200k · $1.23')
})

test('racing measure and turn.complete toast exactly once', async ($, on) => {
  const w = world(on, usageOf(170_000))
  const u = usageOf(170_000)

  await Promise.all([
    $.session.measure(measure(u)),
    $.turn.complete(turnOf({ input_tokens: 1, output_tokens: 1 })),
    $.session.measure(measure(u)),
  ])
  expect(w.toasts).toHaveLength(1)
})

test('/clear re-arms the toast', async ($, on) => {
  const w = world(on, usageOf(170_000))
  const u = usageOf(170_000)

  await $.session.measure(measure(u))
  expect(w.toasts).toHaveLength(1)

  await $.session.end({ reason: 'clear', sessionId: 's', resume: { id: 's' } })
  await $.session.measure(measure(u))
  expect(w.toasts).toHaveLength(2)
})

test('a jump from no reading straight past warnAt toasts, and a missing reading re-arms', async ($, on) => {
  const w = world(on, usageOf(undefined))
  const go = async (tokens: number | undefined) => {
    const u = usageOf(tokens)
    w.seen.usage = u
    await $.session.measure(measure(u))
  }

  await go(undefined)
  expect(w.toasts).toHaveLength(0)
  await go(180_000)
  expect(w.toasts).toHaveLength(1)
  await go(undefined)
  await go(180_000)
  expect(w.toasts).toHaveLength(2)
})
