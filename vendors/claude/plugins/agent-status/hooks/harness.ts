import type { On, RenderElement } from 'claude-code'
import { mock } from 'claude-code/testing'

export const T0 = 1_700_000_000_000
export const SURFACES = ['terminal', 'desktop'] as const

export type Listed = {
  id: string
  type: string
  description: string
  status: 'pending' | 'running' | 'waiting' | 'idle' | 'completed' | 'failed' | 'killed'
  parentId?: string
}

// The engine's side of the world: what `$.agent.list()` answers, what the
// engine draws when the mod passes, and what the mod showed or logged.
export function world(on: On) {
  const clock = mock.clock(on, { now: T0 })
  const w = {
    clock,
    listed: [] as Listed[],
    listCalls: 0,
    nextAgent: 'a1',
    toasts: [] as string[],
    logs: [] as string[],
    commands: [] as string[],
    gate: undefined as Promise<void> | undefined,
    isListFailing: false,
    spawned: [] as unknown[],
    called: [] as unknown[],
  }

  on('agent.list', () => {
    w.listCalls += 1
    return w.isListFailing ? { deny: 'list is down' } : { value: w.listed }
  })
  on('agent.spawn', (_$, e) => {
    w.spawned.push(e)
    return { model: 'sonnet', agentId: w.nextAgent }
  })
  on('session.start', (_$, e) => ({ cwd: e.cwd }))
  on('turn.complete', (_$, e) => ({ text: e.answer }))
  on('tool.call', async (_$, e) => {
    w.called.push(e)
    await w.gate
    return { result: 'ok', text: 'ok' }
  })
  on('ui.toast', (_$, e) => {
    w.toasts.push(e.text)
    return { value: undefined }
  })
  on('ui.log', (_$, e) => {
    w.logs.push(e.text)
    return { value: undefined }
  })
  on('command.register', (_$, e) => {
    w.commands.push(e.name)
    return { value: { command: e.name } }
  })
  on('ui.open', () => ({ value: { isPlaced: true } }))
  on('ui.render', { component: 'AbovePrompt' }, () => h('Text', {}, 'engine band') as RenderElement)

  return w
}

export const BAND = {
  hasSurvey: false,
  isWorking: true,
  maxRows: 12,
  bodyColumns: 60,
  scroll: { offset: 0, bodyRows: 11 },
  view: {},
}

export const PANE = {
  title: 'Agents',
  isFocused: false,
  bodyColumns: 60,
  placement: 'inline' as const,
  scroll: { offset: 0, bodyRows: 20 },
  view: {},
}
