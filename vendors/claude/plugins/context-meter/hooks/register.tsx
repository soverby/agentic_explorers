import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register, SessionContextUsage, SessionCost } from 'claude-code'

import type { ContextTokens, ContextTotals } from '../types'
import { cell, grouped, kfmt, pct1, usd } from './format'

const NONE: ContextTotals = {
  input: 0,
  output: 0,
  cacheRead: 0,
  cacheCreation: 0,
  turns: 0,
}

const lastTurn = atom({ plugin: 'context-meter', key: 'lastTurn' } as const, null)
const totals = atom({ plugin: 'context-meter', key: 'totals' } as const, NONE)
const isWarned = atom({ plugin: 'context-meter', key: 'isWarned' } as const, false)

const DEFAULT_WARN_AT = 80

// `ctx 42% 84k/200k · turn in 12.3k out 1.1k cache 90% · $1.23`
export const statusText = (
  context: SessionContextUsage,
  turn: ContextTokens | null,
  cost: SessionCost | undefined,
): string => {
  const fill = context.tokens === undefined ? '-' : kfmt(context.tokens)
  const percent = context.percent === undefined ? '' : `${context.percent}% `
  const parts = [`ctx ${percent}${fill}/${kfmt(context.window)}`]

  if (turn !== null) {
    const input = turn.input + turn.cacheRead + turn.cacheCreation
    const cache = input > 0 ? ` cache ${Math.round((turn.cacheRead * 100) / input)}%` : ''
    parts.push(`turn in ${kfmt(input)} out ${kfmt(turn.output)}${cache}`)
  }
  if (cost !== undefined) parts.push(usd(cost.usd))

  return parts.join(' · ')
}

type Engine = EngineInterface

// Toast once on the way up; re-arm when the fill drops (compaction, /clear).
// The decision is made inside the compare-and-set, so two events racing on
// the same reading cannot both toast.
async function checkWarn($: Engine, warnAt: number, context: SessionContextUsage) {
  const percent = context.percent ?? 0
  let isFiring = false

  await update($, isWarned, warned => {
    const isOver = percent >= warnAt
    isFiring = isOver && !warned

    return isOver
  })

  if (isFiring) {
    $.ui.toast(
      `Context ${percent}% full (${kfmt(context.tokens ?? 0)}/${kfmt(context.window)}). Consider /compact.`,
      { timeoutMs: 8000 },
    )
  }
}

async function refresh(
  $: Engine,
  warnAt: number,
  context: SessionContextUsage,
  cost: SessionCost | undefined,
) {
  $.ui.status(statusText(context, await read($, lastTurn), cost))
  await checkWarn($, warnAt, context)
}

export const register: Register = (on, options) => {
  const rawWarn = options.warnAt
  const warnAt =
    typeof rawWarn === 'number' && rawWarn >= 1 && rawWarn <= 100 ? rawWarn : DEFAULT_WARN_AT

  // Status first: a failed command registration must not skip the first line.
  on('session.start', async ($, e, next) => {
    try {
      const { context, cost } = await $.session.usage()
      await refresh($, warnAt, context, cost)
    } finally {
      await $.command.register({
        name: 'ctx',
        description: 'Context breakdown, session cost and token totals',
      })
    }

    return next(e)
  }).catch(($, e, next) => next(e))

  // A /clear raises no session.start, so a turn start also puts the line back.
  on('turn.start', async ($, e, next) => {
    const { context, cost } = await $.session.usage()
    await refresh($, warnAt, context, cost)

    return next(e)
  }).catch(($, e, next) => next(e))

  on('session.measure', async ($, e, next) => {
    await refresh($, warnAt, e.context, e.cost)

    return next(e)
  }).catch(($, e, next) => next(e))

  // Main loop only: a subagent's turn carries `agentId` and is not this window.
  on('turn.complete', async ($, e, next) => {
    if (e.agentId === undefined && e.usage !== undefined) {
      const turn: ContextTokens = {
        input: e.usage.input_tokens,
        output: e.usage.output_tokens,
        cacheRead: e.usage.cache_read_input_tokens,
        cacheCreation: e.usage.cache_creation_input_tokens,
      }
      await update($, lastTurn, () => turn)
      await update($, totals, sum => ({
        input: sum.input + turn.input,
        output: sum.output + turn.output,
        cacheRead: sum.cacheRead + turn.cacheRead,
        cacheCreation: sum.cacheCreation + turn.cacheCreation,
        turns: sum.turns + 1,
      }))
      const { context, cost } = await $.session.usage()
      await refresh($, warnAt, context, cost)
    }

    return next(e)
  }).catch(($, e, next) => next(e))

  // /clear starts a new conversation: its figures start over too.
  on('session.end', async ($, e, next) => {
    if (e.reason === 'clear') {
      await update($, lastTurn, () => null)
      await update($, totals, () => NONE)
      await update($, isWarned, () => false)
      $.ui.status(undefined)
    }

    return next(e)
  }).catch(($, e, next) => next(e))

  on('command.run', { command: 'ctx' }, async $ => {
    const usage = await $.session.usage({ breakdown: 'summary' })
    const sum = await read($, totals)
    const { context, cost } = usage
    const breakdown = context.breakdown
    const lines: string[] = []

    const live =
      context.tokens === undefined
        ? 'no response in this window yet'
        : `${grouped(context.tokens)} of ${grouped(context.window)} tokens${
            context.percent === undefined ? '' : ` (${context.percent}%)`
          }`
    lines.push(`**Context** ${live}`, '')

    if (breakdown === undefined) {
      lines.push('No breakdown is available for this session.')
    } else {
      const max = breakdown.rawMaxTokens
      lines.push('| Category | Tokens | % of window |', '| --- | ---: | ---: |')
      for (const row of breakdown.categories) {
        const isDeferred = row.kind === 'deferred'
        lines.push(
          `| ${cell(row.name)}${isDeferred && !/deferred/i.test(row.name) ? ' (deferred)' : ''} | ${grouped(row.tokens)} | ${
            isDeferred ? '-' : pct1(row.tokens, max)
          } |`,
        )
      }
      lines.push(
        `| **Total used** | **${grouped(breakdown.totalTokens)}** | **${pct1(breakdown.totalTokens, max)}** |`,
        '',
        `${breakdown.autocompactSource === 'auto' ? 'Measured against' : 'Compaction window'} ${grouped(max)} tokens (model window ${grouped(context.window)}), model ${breakdown.model}. The table is an estimate; the live figure above is the last response's.`,
      )
    }

    lines.push(
      '',
      `**Session cost** ${cost === undefined ? 'not tracked' : usd(cost.usd)}`,
      '',
      `**Main-loop tokens since the mod loaded** (${sum.turns} turns)`,
      '',
      '| Input | Output | Cache read | Cache write |',
      '| ---: | ---: | ---: | ---: |',
      `| ${grouped(sum.input)} | ${grouped(sum.output)} | ${grouped(sum.cacheRead)} | ${grouped(sum.cacheCreation)} |`,
    )

    return { text: lines.join('\n') }
  })
}
