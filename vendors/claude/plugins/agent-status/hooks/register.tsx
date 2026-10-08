import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { AgentRow } from '../types'
import {
  FRAME_MS,
  GRACE_MS,
  MAX_ROWS,
  allRows,
  anyActive,
  blankRow,
  colorFor,
  critterFrame,
  fit,
  formatDuration,
  formatTokens,
  glyphFor,
  isActive,
  mergeList,
  shortTool,
  totalTokens,
  visibleRows,
} from './model'
import type { Agents } from './model'

const PANE = 'agent-status'
// `/agents` is a Claude Code built-in a plugin may not take, so the command is /agent-status.
const DESCRIPTION = 'Show every agent of this session in a pane'
const REFRESH_MS = 2000
const MAX_DEPTH = 3

type Dollar = EngineInterface

const agents = atom({ plugin: 'agent-status', key: 'agents' } as const, {})
const nowAt = atom({ plugin: 'agent-status', key: 'now' } as const, 0)

// Module state: a reload runs this file fresh, so timers start again on the
// next event that sees an active agent. Everything drawn lives in $.state.
let isCritters = false
let timer: { cancel: () => void } | undefined
let grace: { cancel: () => void } | undefined
let ticks = 0
// Untracked loop ids already looked up once, so an engine fork costs one list call.
const checked = new Set<string>()

// Critters need a frame clock; the list is still read about every 2 s.
const periodMs = (): number => (isCritters ? FRAME_MS : REFRESH_MS)

async function safe($: Dollar, label: string, work: () => Promise<void>): Promise<void> {
  try {
    await work()
  } catch (error) {
    $.ui.log(`agent-status: ${label}: ${String(error)}`, { to: 'debug' })
  }
}

function stopTimer(): void {
  timer?.cancel()
  timer = undefined
}

function stopGrace(): void {
  grace?.cancel()
  grace = undefined
}

function startTimer($: Dollar): void {
  if (timer !== undefined) return
  ticks = 0
  timer = $.clock.every(periodMs(), () => {
    void runTick($)
  })
}

// A tick that fails drops the timer, whose `$` may be stale; the next event
// that sees an active agent starts a fresh one.
async function runTick($: Dollar): Promise<void> {
  try {
    await tick($)
  } catch (error) {
    stopTimer()
    $.ui.log(`agent-status: tick: ${String(error)}`, { to: 'debug' })
  }
}

// One last redraw once the newest ended row is past its grace window.
function startGrace($: Dollar): void {
  stopGrace()
  grace = $.clock.after(GRACE_MS + 250, () => {
    grace = undefined
    void safe($, 'grace', async () => {
      const t = await $.clock.now()
      await update($, nowAt, () => t)
    })
  })
}

// The timer runs only while an agent is active.
async function settle($: Dollar, rows: Agents): Promise<void> {
  if (anyActive(rows)) {
    stopGrace()
    startTimer($)
    return
  }
  stopTimer()
  const t = await $.clock.now()
  if (visibleRows(rows, t).length > 0) startGrace($)
}

async function refresh($: Dollar): Promise<void> {
  const list = await $.agent.list()
  const t = await $.clock.now()
  let ended: AgentRow[] = []
  // The updater can run again on a version miss: keep the last run's `ended`.
  const rows = await update($, agents, cur => {
    const merged = mergeList(cur, list, t)
    ended = merged.ended
    return merged.next
  })
  await update($, nowAt, () => t)
  for (const row of ended) $.ui.toast(toastText(row, t))
  await settle($, rows)
}

async function tick($: Dollar): Promise<void> {
  ticks += 1
  const t = await $.clock.now()
  await update($, nowAt, () => t)
  if (ticks % (REFRESH_MS / periodMs()) === 0) await refresh($)
}

// Spawns create a row; tool calls and turns only update one the mod already
// tracks. An id the list never names (an engine fork) must not make a phantom.
const upsert =
  (id: string, t: number, edit: (row: AgentRow) => AgentRow) =>
  (cur: Agents): Agents => ({ ...cur, [id]: edit(cur[id] ?? blankRow(id, t)) })

const existing =
  (id: string, edit: (row: AgentRow) => AgentRow) =>
  (cur: Agents): Agents => {
    const row = cur[id]
    return row === undefined ? cur : { ...cur, [id]: edit(row) }
  }

async function openPane($: Dollar): Promise<{ text: string }> {
  await safe($, 'refresh', () => refresh($))
  const opened = await $.ui.open({ id: PANE, title: 'Agents' })

  return {
    text: opened.isPlaced ? 'Agents pane opened.' : `Agents pane not shown: ${opened.reason}`,
  }
}

export const register: Register = (on, options) => {
  const style = options.style
  if (style !== 'plain' && style !== 'critters') {
    throw new Error(`agent-status: style must be "plain" or "critters", got ${String(style)}`)
  }
  isCritters = style === 'critters'

  on('session.start', async ($, e, next) => {
    await safe($, 'register /agent-status', async () => {
      await $.command.register({ name: 'agent-status', description: DESCRIPTION })
    })
    await safe($, 'seed', () => refresh($))

    return next(e)
  })

  on('agent.spawn', async ($, e, next) => {
    const ran = await next(e)
    const id = ran.deny === undefined ? ran.agentId : undefined

    if (id !== undefined) {
      await safe($, 'spawn', async () => {
        const t = await $.clock.now()
        await update(
          $,
          agents,
          upsert(id, t, row => ({
            ...row,
            type: e.subagentType,
            description: e.description,
            parentId: row.parentId ?? e.parentAgentId ?? null,
          })),
        )
        await refresh($)
      })
    }

    return ran
  })

  on('tool.call', async ($, e, next) => {
    const id = e.agentId
    if (id === undefined) return next(e)

    await safe($, 'tool start', async () => {
      // A loop started without agent.spawn (a forked skill) is picked up from the list.
      if ((await read($, agents))[id] === undefined) {
        if (checked.has(id)) return
        checked.add(id)
        await refresh($)
        if ((await read($, agents))[id] === undefined) return
      }
      const rows = await update(
        $,
        agents,
        existing(id, row => ({
          ...row,
          toolCount: row.toolCount + 1,
          calls: [...row.calls, { id: e.tool_use_id, tool: e.tool }],
        })),
      )
      await settle($, rows)
    })

    try {
      return await next(e)
    } finally {
      await safe($, 'tool end', async () => {
        if ((await read($, agents))[id] === undefined) return
        await update(
          $,
          agents,
          existing(id, row => ({ ...row, calls: row.calls.filter(call => call.id !== e.tool_use_id) })),
        )
      })
    }
  })

  on('turn.complete', async ($, e, next) => {
    const ran = await next(e)
    const id = e.agentId

    if (id !== undefined) {
      await safe($, 'turn', async () => {
        const usage = e.usage
        // An agent listed but not yet tracked is picked up before its usage is added.
        const isTracked = (await read($, agents))[id] !== undefined
        if (!isTracked) await refresh($)
        if (usage !== undefined) {
          await update(
            $,
            agents,
            existing(id, row => ({
              ...row,
              tokens: {
                input: row.tokens.input + usage.input_tokens,
                output: row.tokens.output + usage.output_tokens,
                cacheRead: row.tokens.cacheRead + usage.cache_read_input_tokens,
                cacheWrite: row.tokens.cacheWrite + usage.cache_creation_input_tokens,
              },
            })),
          )
        }
        if (isTracked) await refresh($)
      })
    }

    return ran
  })

  on('command.run', { command: 'agent-status' }, $ => openPane($))

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) return next(e)

    const rows = visibleRows(await read($, agents), await read($, nowAt))
    if (rows.length === 0) return next(e)

    const now = await read($, nowAt)
    // Draw above what the hooks beneath draw (another mod's band), never instead of it.
    const below = await next(e)
    const { Box, Text } = $.ui.resolve(e)
    const cols = Math.max(20, e.props.bodyColumns)
    const room = Math.max(1, Math.min(MAX_ROWS, e.props.maxRows - 1))
    const shown = rows.slice(0, room)
    const more = rows.length - shown.length

    return (
      <Box flexDirection="column">
        {shown.map(({ row, depth }) => {
          const line = bandLine(row, depth, now, cols, isCritters)

          return (
            <Box key={`agent-${row.id}`}>
              <Text color={colorFor(row.status)} wrap="truncate">
                {line.lead}
              </Text>
              <Text bold wrap="truncate">
                {line.type}
              </Text>
              <Text dimColor wrap="truncate">
                {line.desc}
              </Text>
              <Text dimColor wrap="truncate">
                {line.right}
              </Text>
            </Box>
          )
        })}
        {more > 0 && (
          <Text dimColor key="more">
            +{more} more
          </Text>
        )}
        {below}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const rows = allRows(await read($, agents))
    const now = await read($, nowAt)
    const { Box, Text } = $.ui.resolve(e)
    const cols = Math.max(20, e.props.bodyColumns)
    const byId = await read($, agents)
    const activeCount = rows.filter(({ row }) => isActive(row.status)).length

    return (
      <Box flexDirection="column">
        <Text bold wrap="truncate">
          {fit(`${rows.length} agents, ${activeCount} active`, cols)}
        </Text>
        {rows.length === 0 && <Text dimColor>No agents yet in this session.</Text>}
        {rows.map(({ row, depth }) => {
          const lead = '  '.repeat(Math.min(depth, MAX_DEPTH))
          const glyph = glyphCell(row, now, isCritters)
          const head = `${lead}${glyph} `
          const parent = row.parentId === null ? 'main' : (byId[row.parentId]?.type ?? row.parentId.slice(0, 8))
          const end = row.endedAt ?? now
          const stats = [
            row.status,
            formatDuration(end - row.startedAt),
            `${row.toolCount} ${row.toolCount === 1 ? 'tool' : 'tools'}`,
            `${formatTokens(totalTokens(row.tokens))} tok`,
          ].join(' · ')
          const from = `parent ${fit(parent, 24)}`
          const indent = ' '.repeat(head.length)
          // Parent joins the stats line when it fits, else it takes a line of its own.
          const details =
            indent.length + stats.length + 3 + from.length <= cols
              ? [`${stats} · ${from}`]
              : [stats, from]

          return (
            <Box key={`row-${row.id}`} flexDirection="column">
              <Box>
                <Text color={colorFor(row.status)} wrap="truncate">
                  {head}
                </Text>
                <Text bold wrap="truncate">
                  {fit(row.type, 16)}
                </Text>
                <Text wrap="truncate">{` ${fit(row.description, Math.max(0, cols - head.length - 18))}`}</Text>
              </Box>
              {details.map((line, i) => (
                <Box key={`${row.id}-line-${i}`}>
                  <Text dimColor wrap="truncate">
                    {`${indent}${fit(line, cols - indent.length)}`}
                  </Text>
                </Box>
              ))}
            </Box>
          )
        })}
      </Box>
    )
  })
}

function toastText(row: AgentRow, now: number): string {
  const took = formatDuration((row.endedAt ?? now) - row.startedAt)
  const text = `${glyphFor(row.status)} ${row.type} ${row.status}: ${row.description}`

  return fit(`${text} (${took}, ${formatTokens(totalTokens(row.tokens))} tok)`, 120)
}

// An animated creature for a live agent in critters mode, else the status glyph.
function glyphCell(row: AgentRow, now: number, isCritters: boolean): string {
  return isCritters && isActive(row.status) ? critterFrame(row.id, now) : glyphFor(row.status)
}

function bandLine(row: AgentRow, depth: number, now: number, cols: number, isCritters: boolean) {
  const lead = `${'  '.repeat(Math.min(depth, MAX_DEPTH))}${glyphCell(row, now, isCritters)} `
  const tool = row.calls.length > 0 ? shortTool(row.calls[row.calls.length - 1]?.tool ?? '') : ''
  const elapsed = formatDuration((row.endedAt ?? now) - row.startedAt)
  const tokens = `${formatTokens(totalTokens(row.tokens))} tok`
  const budget = cols - lead.length

  // Drop the current tool, then the token count, when the row would not fit.
  const options = [
    [fit(tool, 18), elapsed, tokens],
    [elapsed, tokens],
    [elapsed],
    [],
  ]
  const parts = options.find(p => budget - p.filter(Boolean).join(' · ').length >= 20) ?? []
  const right = parts.filter(Boolean).join(' · ')
  const left = right === '' ? budget : budget - right.length - 1
  const type = fit(row.type, Math.min(14, left))
  const descRoom = left - type.length - 1
  const desc = descRoom > 0 ? fit(row.description, descRoom) : ''
  const used = type.length + (desc === '' ? 0 : 1 + desc.length)

  return {
    lead,
    type,
    desc: desc === '' ? '' : ` ${desc}`,
    right: right === '' ? '' : `${' '.repeat(Math.max(1, left - used + 1))}${right}`,
  }
}

