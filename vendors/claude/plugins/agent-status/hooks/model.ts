import type { AgentRow, AgentStatus, AgentTokens } from '../types'

export const GRACE_MS = 10_000
export const MAX_ROWS = 6
export const FRAME_MS = 500
// A spawned agent the list never names (an engine fork, a workflow agent) is
// dropped after this many refreshes.
export const MAX_MISSES = 4

const ACTIVE: readonly AgentStatus[] = ['pending', 'running', 'waiting']
const ENDED: readonly AgentStatus[] = ['completed', 'failed', 'killed', 'ended']

export const isActive = (s: AgentStatus): boolean => ACTIVE.includes(s)
export const isEnded = (s: AgentStatus): boolean => ENDED.includes(s)

// What a list entry carries; AgentInfo from `$.agent.list()` fits it.
export type ListedAgent = {
  id: string
  type: string
  description: string
  status: AgentStatus
  parentId?: string
}

const GLYPHS: Record<AgentStatus, string> = {
  pending: '◌',
  running: '●',
  waiting: '◐',
  idle: '○',
  completed: '✓',
  failed: '✗',
  killed: '⊘',
  ended: '–',
}

const COLORS: Record<AgentStatus, string> = {
  pending: 'inactive',
  running: 'claude',
  waiting: 'warning',
  idle: 'inactive',
  completed: 'success',
  failed: 'error',
  killed: 'inactive',
  ended: 'inactive',
}

// A status with no entry is an error, never a default glyph.
export function glyphFor(status: AgentStatus): string {
  const glyph = GLYPHS[status]
  if (glyph === undefined) throw new Error(`agent-status: no glyph for status "${status}"`)
  return glyph
}

export function colorFor(status: AgentStatus): string {
  const color = COLORS[status]
  if (color === undefined) throw new Error(`agent-status: no color for status "${status}"`)
  return color
}

// Five cells wide, ASCII only, so every terminal measures them alike.
const CRITTERS: readonly (readonly string[])[] = [
  ['><>  ', ' ><> ', '  ><>', ' ><> '],
  ['~~~o ', '~~o~ ', '~o~~ ', 'o~~~ '],
  ['^v^  ', 'v^v  '],
  ['(o,o)', '(-,-)', '(o,o)', '(o,o)'],
]
export const CRITTER_WIDTH = 5

export function critterFrame(id: string, now: number): string {
  let sum = 0
  for (let i = 0; i < id.length; i += 1) sum += id.charCodeAt(i)
  const frames = CRITTERS[sum % CRITTERS.length]
  if (frames === undefined) throw new Error('agent-status: no critter frames')
  const frame = frames[Math.floor(now / FRAME_MS) % frames.length]
  if (frame === undefined) throw new Error('agent-status: no critter frame')
  return frame.padEnd(CRITTER_WIDTH)
}

// 999, 1.2k, 12k, 999k, 1.2M: integer math, floored.
export function formatTokens(n: number): string {
  const count = Math.max(0, Math.floor(n))
  if (count < 1000) return String(count)
  if (count < 10_000) {
    const tenths = Math.floor(count / 100)
    return `${Math.floor(tenths / 10)}.${tenths % 10}k`
  }
  if (count < 1_000_000) return `${Math.floor(count / 1000)}k`
  const tenths = Math.floor(count / 100_000)
  return `${Math.floor(tenths / 10)}.${tenths % 10}M`
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  if (total < 60) return `${total}s`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes}m${String(total % 60).padStart(2, '0')}s`
  return `${Math.floor(minutes / 60)}h${String(minutes % 60).padStart(2, '0')}m`
}

// One line, no control characters, cut to `width` cells with an ellipsis.
export function fit(text: string, width: number): string {
  const line = text.replace(/[\u0000-\u001f\u007f]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (width <= 0) return ''
  if (line.length <= width) return line
  if (width === 1) return '…'
  return `${line.slice(0, width - 1)}…`
}

// Tokens an agent has spent. Cache reads re-count the same context every
// step, so they are kept in state but left out of the sum.
export const totalTokens = (t: AgentTokens): number => t.input + t.output + t.cacheWrite

export const shortTool = (tool: string): string =>
  tool.startsWith('mcp__') ? (tool.split('__').pop() ?? tool) : tool

export const emptyTokens = (): AgentTokens => ({ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 })

export function blankRow(id: string, now: number): AgentRow {
  return {
    id,
    type: 'agent',
    description: '',
    status: 'pending',
    parentId: null,
    startedAt: now,
    endedAt: null,
    toolCount: 0,
    calls: [],
    tokens: emptyTokens(),
    isSeen: false,
    misses: 0,
    isNotified: false,
  }
}

export type Agents = Record<string, AgentRow>

// Folds `$.agent.list()` into the tracked rows. `ended` holds the rows that
// just reached an end state and have not had their toast yet.
export function mergeList(
  cur: Agents,
  list: readonly ListedAgent[],
  now: number,
): { next: Agents; ended: AgentRow[] } {
  const next: Agents = { ...cur }
  const ended: AgentRow[] = []
  const listed = new Set<string>()

  const settle = (row: AgentRow): AgentRow => {
    let out = row
    if (isEnded(out.status)) {
      if (out.endedAt === null) out = { ...out, endedAt: now }
      if (!out.isNotified) {
        out = { ...out, isNotified: true }
        ended.push(out)
      }
    } else if (out.endedAt !== null) {
      out = { ...out, endedAt: null }
    }
    return out
  }

  for (const info of list) {
    listed.add(info.id)
    const row = next[info.id] ?? blankRow(info.id, now)
    next[info.id] = settle({
      ...row,
      type: info.type,
      description: info.description,
      status: info.status,
      parentId: info.parentId ?? null,
      isSeen: true,
    })
  }

  for (const row of Object.values(cur)) {
    if (listed.has(row.id) || isEnded(row.status)) continue
    if (!row.isSeen) {
      // Never listed: an engine fork or workflow agent. Not tracked, so it
      // cannot keep the timer running; give it a few refreshes, then drop it.
      if (row.misses + 1 >= MAX_MISSES) delete next[row.id]
      else next[row.id] = { ...row, misses: row.misses + 1 }
      continue
    }
    // Seen, then gone: the engine drops an ended agent's task after a while.
    next[row.id] = settle({ ...row, status: 'ended' })
  }

  return { next, ended }
}

export const anyActive = (agents: Agents): boolean =>
  Object.values(agents).some(row => isActive(row.status))

// Active agents plus agents that ended within GRACE_MS, parents before their
// children. A child whose parent is not shown counts as a root.
export function visibleRows(agents: Agents, now: number): { row: AgentRow; depth: number }[] {
  return tree(
    Object.values(agents).filter(
      row => isActive(row.status) || (row.endedAt !== null && now - row.endedAt <= GRACE_MS),
    ),
  )
}

export function allRows(agents: Agents): { row: AgentRow; depth: number }[] {
  return tree(Object.values(agents))
}

function tree(rows: AgentRow[]): { row: AgentRow; depth: number }[] {
  const byStart = [...rows].sort((a, b) => a.startedAt - b.startedAt || a.id.localeCompare(b.id))
  const shown = new Set(byStart.map(row => row.id))
  const out: { row: AgentRow; depth: number }[] = []
  const placed = new Set<string>()

  const place = (row: AgentRow, depth: number): void => {
    if (placed.has(row.id)) return
    placed.add(row.id)
    out.push({ row, depth })
    for (const child of byStart) if (child.parentId === row.id) place(child, depth + 1)
  }

  for (const row of byStart) {
    if (row.parentId === null || !shown.has(row.parentId)) place(row, 0)
  }
  // A parent cycle would leave rows unplaced; show them rather than lose them.
  for (const row of byStart) place(row, 0)
  return out
}
