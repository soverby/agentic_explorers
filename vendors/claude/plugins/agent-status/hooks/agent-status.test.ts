import type { AgentSpawnInput, RenderElement, ToolCallArgs, TurnCompleteInput } from 'claude-code'
import { expect, test } from 'claude-code/testing'
import type { Engine, Plugin } from 'claude-code/testing'

import { BAND, PANE, SURFACES, world } from './harness'
import type { Listed } from './harness'

const ENGINE = { plugin: 'engine', tier: 'core' } as const

const spawnInput = (description: string, subagentType: string, parentAgentId?: string): AgentSpawnInput => ({
  tool_use_id: 'toolu_spawn',
  prompt: 'do it',
  description,
  subagentType,
  provider: ENGINE,
  parentModel: 'sonnet',
  background: true,
  fork: false,
  ...(parentAgentId === undefined ? {} : { parentAgentId }),
})

const turnInput = (agentId: string, usage: [number, number, number, number]): TurnCompleteInput => ({
  answer: 'done',
  durationMs: 1000,
  isAborted: false,
  turnId: `turn-${agentId}`,
  agentId,
  reason: 'answer',
  usage: {
    model: 'sonnet',
    input_tokens: usage[0],
    output_tokens: usage[1],
    cache_read_input_tokens: usage[2],
    cache_creation_input_tokens: usage[3],
  },
})

// `$.tool.call` types its input without agentId, but the engine passes it on
// to the hooks, which is how a subagent's call reaches them.
const agentCall = (agentId: string, tool: string): ToolCallArgs =>
  ({ tool, file_path: '/tmp/x', agentId }) as unknown as ToolCallArgs

const running = (id: string, type: string, description: string, parentId?: string): Listed => ({
  id,
  type,
  description,
  status: 'running',
  ...(parentId === undefined ? {} : { parentId }),
})

test('band shows a row per active agent and hides with none, on terminal and desktop', async ($, on) => {
  const w = world(on)

  for (const surface of SURFACES) {
    const idle = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    expect(await idle.find({ text: 'engine band' })).toBeDefined()
    expect(await idle.find({ type: 'Text', text: /Explore/ })).toBeUndefined()
    await idle.unmount()
  }

  w.listed = [running('a1', 'Explore', 'Find auth code')]
  await $.agent.spawn(spawnInput('Find auth code', 'Explore'))

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    // The engine's own band stays, drawn below ours.
    expect(await ui.find({ text: 'engine band' })).toBeDefined()
    const row = await ui.find({ key: 'agent-a1' })
    expect(row?.text).toContain('Explore')
    expect(row?.text).toContain('Find auth code')
    expect(row?.text).toContain('0s')
    expect(row?.text).toContain('0 tok')
    await ui.unmount()
  }
})

test('band yields to a survey', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'Find auth code')]
  await $.agent.spawn(spawnInput('Find auth code', 'Explore'))

  const ui = await $.ui.mount({
    plugin: 'agent-status',
    surface: 'terminal',
    component: 'AbovePrompt',
    props: { ...BAND, hasSurvey: true },
  })
  expect(await ui.find({ text: 'engine band' })).toBeDefined()
})

test('current tool shows while a call runs and clears on its result; tools are counted', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'Find auth code')]
  await $.agent.spawn(spawnInput('Find auth code', 'Explore'))

  let release = (): void => {}
  w.gate = new Promise<void>(resolve => {
    release = resolve
  })
  const call = $.tool.call(agentCall('a1', 'Read'))
  await w.clock.settle()

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    expect((await ui.find({ key: 'agent-a1' }))?.text).toContain('Read')
    await ui.unmount()
  }

  release()
  const result = await call
  expect(result).toMatchObject({ result: 'ok' })

  const after = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect((await after.find({ key: 'agent-a1' }))?.text).not.toContain('Read')
  await after.unmount()

  w.gate = undefined
  await $.tool.call(agentCall('a1', 'Grep'))
  await $.tool.call(agentCall('a1', 'Read'))

  const pane = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'Pane', requestId: 'agent-status', props: PANE })
  expect((await pane.find({ key: 'row-a1' }))?.text).toContain('3 tools')
})

test('a main-loop tool call (no agentId) is left alone', async ($, on) => {
  const w = world(on)
  await $.tool.call({ tool: 'Read', file_path: '/tmp/x' })
  expect(w.listCalls).toBe(0)
  const ui = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect(await ui.find({ text: 'engine band' })).toBeDefined()
})

test('tokens are summed per agentId and shown in k-format', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'First'), running('a2', 'Plan', 'Second')]
  w.nextAgent = 'a1'
  await $.agent.spawn(spawnInput('First', 'Explore'))
  w.nextAgent = 'a2'
  await $.agent.spawn(spawnInput('Second', 'Plan'))

  // [input, output, cacheRead, cacheWrite]; cache reads are not summed into the shown total.
  await $.turn.complete(turnInput('a1', [1000, 200, 50_000, 300]))
  await $.turn.complete(turnInput('a1', [2000, 500, 60_000, 0]))
  await $.turn.complete(turnInput('a2', [40, 10, 0, 0]))

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    // a1: 1000+200+300 + 2000+500+0 = 4000 -> 4.0k; a2: 50 -> 50
    expect((await ui.find({ key: 'agent-a1' }))?.text).toContain('4.0k tok')
    expect((await ui.find({ key: 'agent-a2' }))?.text).toContain('50 tok')
    await ui.unmount()
  }
})

test('a turn with no usage adds nothing and a main-loop turn is ignored', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'First')]
  await $.agent.spawn(spawnInput('First', 'Explore'))
  const bare = turnInput('a1', [0, 0, 0, 0])
  const { usage: _usage, ...noUsage } = bare
  await $.turn.complete(noUsage as TurnCompleteInput)
  const main = { ...turnInput('a1', [9000, 9000, 0, 0]) }
  delete main.agentId
  await $.turn.complete(main)

  const ui = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect((await ui.find({ key: 'agent-a1' }))?.text).toContain('0 tok')
})

test('children are indented under their parent', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'general-purpose', 'Parent task'), running('a2', 'Explore', 'Child task', 'a1')]
  w.nextAgent = 'a1'
  await $.agent.spawn(spawnInput('Parent task', 'general-purpose'))
  w.nextAgent = 'a2'
  await $.agent.spawn(spawnInput('Child task', 'Explore', 'a1'))

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    const parent = await ui.find({ key: 'agent-a1' })
    const child = await ui.find({ key: 'agent-a2' })
    expect(parent?.text.startsWith('● ')).toBe(true)
    expect(child?.text.startsWith('  ● ')).toBe(true)
    await ui.unmount()
  }
})

test('rows are capped at 6 with "+N more" and fit 60 columns', async ($, on) => {
  const w = world(on)
  const long = 'An extremely long description that cannot possibly fit in sixty columns of text'
  w.listed = Array.from({ length: 8 }, (_, i) => running(`a${i + 1}`, 'general-purpose', long))
  for (const l of w.listed) {
    w.nextAgent = l.id
    await $.agent.spawn(spawnInput(long, 'general-purpose'))
  }

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    for (let i = 1; i <= 6; i += 1) {
      const row = await ui.find({ key: `agent-a${i}` })
      expect(row).toBeDefined()
      expect(row?.text.length).toBeLessThanOrEqual(60)
    }
    expect(await ui.find({ key: 'agent-a7' })).toBeUndefined()
    expect((await ui.find({ type: 'Text', text: /more$/ }))?.text).toBe('+2 more')
    await ui.unmount()
  }
})

test('rows still fit at a narrow 24 columns with a tool and tokens', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'general-purpose', 'Refactor the whole thing')]
  await $.agent.spawn(spawnInput('x', 'x'))
  await $.turn.complete(turnInput('a1', [123_456, 7_890, 0, 0]))
  let release = (): void => {}
  w.gate = new Promise<void>(resolve => {
    release = resolve
  })
  const call = $.tool.call(agentCall('a1', 'mcp__server__some_long_tool_name'))
  await w.clock.settle()

  const ui = await $.ui.mount({
    plugin: 'agent-status',
    surface: 'terminal',
    component: 'AbovePrompt',
    props: { ...BAND, bodyColumns: 24 },
  })
  expect((await ui.find({ key: 'agent-a1' }))?.text.length).toBeLessThanOrEqual(24)
  release()
  await call
})

test('a toast is raised once per agent that completes, fails or is killed', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One'), running('a2', 'Plan', 'Two'), running('a3', 'Plan', 'Three')]
  for (const id of ['a1', 'a2', 'a3']) {
    w.nextAgent = id
    await $.agent.spawn(spawnInput(id, 'Plan'))
  }
  expect(w.toasts).toEqual([])

  w.listed = [
    { ...running('a1', 'Explore', 'One'), status: 'completed' },
    { ...running('a2', 'Plan', 'Two'), status: 'failed' },
    { ...running('a3', 'Plan', 'Three'), status: 'killed' },
  ]
  await w.clock.advance(2000)
  expect(w.toasts).toHaveLength(3)
  expect(w.toasts[0]).toContain('Explore completed: One')
  expect(w.toasts[1]).toContain('Plan failed: Two')
  expect(w.toasts[2]).toContain('Plan killed: Three')

  // More refreshes and turn completions raise no second toast.
  await $.turn.complete(turnInput('a1', [1, 1, 0, 0]))
  await w.clock.advance(60_000)
  expect(w.toasts).toHaveLength(3)
})

test('ended rows stay 10 s, the refresh timer stops with no active agent', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))

  await w.clock.advance(6000)
  const calls = w.listCalls
  expect(calls).toBeGreaterThanOrEqual(3) // spawn + 3 ticks of 2 s

  w.listed = [{ ...running('a1', 'Explore', 'One'), status: 'completed' }]
  await w.clock.advance(2000)

  for (const surface of SURFACES) {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
    expect((await ui.find({ key: 'agent-a1' }))?.text).toContain('✓')
    await ui.unmount()
  }

  const stopped = w.listCalls
  await w.clock.advance(20_000)
  expect(w.listCalls).toBe(stopped) // timer stopped: no list calls while nothing is active

  const gone = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect(await gone.find({ key: 'agent-a1' })).toBeUndefined()
  expect(await gone.find({ text: 'engine band' })).toBeDefined()
})

test('/agent-status opens a pane listing every agent, ended ones included', async ($, on) => {
  const w = world(on)
  w.listed = [
    running('a1', 'general-purpose', 'Lead'),
    running('a2', 'Explore', 'Scout', 'a1'),
    { ...running('a3', 'Plan', 'Planner'), status: 'completed' },
    { ...running('a4', 'Plan', 'Broken'), status: 'failed' },
    { ...running('a5', 'Plan', 'Stopped'), status: 'killed' },
  ]
  for (const l of w.listed) {
    w.nextAgent = l.id
    await $.agent.spawn(spawnInput(l.description, l.type))
  }
  await $.agent.spawn(spawnInput('x', 'x'))
  await $.tool.call(agentCall('a2', 'Read'))
  await $.turn.complete(turnInput('a2', [1500, 500, 0, 0]))
  await w.clock.advance(65_000)

  await $.session.start({ cwd: '/tmp', surface: 'terminal', isInteractive: true })
  expect(w.commands).toEqual(['agent-status'])

  const ran = await $.command.run({ command: 'agent-status', args: '', origin: { kind: 'plugin', name: 'test' }, presentation: { isFullscreen: false, columns: 80 } })
  expect(ran.text).toBe('Agents pane opened.')

  for (const surface of SURFACES) {
    const pane = await $.ui.mount({ plugin: 'agent-status', surface, component: 'Pane', requestId: 'agent-status', props: PANE })
    expect((await pane.find({ type: 'Text', text: /5 agents, 2 active/ }))).toBeDefined()
    const scout = (await pane.find({ key: 'row-a2' }))?.text ?? ''
    expect(scout).toContain('Explore')
    expect(scout).toContain('Scout')
    expect(scout).toContain('running')
    expect(scout).toContain('1 tool ')
    expect(scout).toContain('2.0k tok')
    expect(scout).toContain('parent general-purpose')
    expect(scout).toMatch(/1m\d\ds/)
    expect((await pane.find({ key: 'row-a1' }))?.text).toContain('parent main')
    expect((await pane.find({ key: 'row-a3' }))?.text).toContain('completed')
    expect((await pane.find({ key: 'row-a4' }))?.text).toContain('failed')
    expect((await pane.find({ key: 'row-a5' }))?.text).toContain('killed')
    for (const line of await pane.findAll({ type: 'Text' })) {
      expect(line.text.length).toBeLessThanOrEqual(PANE.bodyColumns)
    }
    await pane.unmount()
  }
})

test('the pane says so when there are no agents', async ($, on) => {
  world(on)
  const pane = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'Pane', requestId: 'agent-status', props: PANE })
  expect(await pane.find({ type: 'Text', text: 'No agents yet in this session.' })).toBeDefined()
})

test('critters style animates a creature from the clock', { options: { style: 'critters' } }, async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))
  const head = async () => {
    const ui = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
    const text = (await ui.find({ key: 'agent-a1' }))?.text ?? ''
    await ui.unmount()
    return text.slice(0, 5)
  }
  const a = await head()
  await w.clock.advance(500)
  const b = await head()
  expect(a).not.toBe(b)
  expect(a).not.toContain('●')
})

test('plain style (default) shows the status glyph and does not animate', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))
  const ui = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props: BAND })
  expect((await ui.find({ key: 'agent-a1' }))?.text.startsWith('● Explore')).toBe(true)
})

test('observe-only: results and events pass through untouched', async ($, on) => {
  const w = world(on)
  w.nextAgent = 'a9'
  const input = spawnInput('Look', 'Explore')
  const spawned = await $.agent.spawn(input)
  expect(spawned).toEqual({ model: 'sonnet', agentId: 'a9' })
  expect(w.spawned[0]).toMatchObject({ prompt: input.prompt, description: 'Look', subagentType: 'Explore' })

  const result = await $.tool.call(agentCall('a9', 'Read'))
  expect(result).toEqual({ result: 'ok', text: 'ok' })
  expect(w.called[0]).toMatchObject({ tool: 'Read', file_path: '/tmp/x', agentId: 'a9' })

  const turn = await $.turn.complete(turnInput('a9', [1, 2, 3, 4]))
  expect(turn).toEqual({ text: 'done' })
})

test('a failing agent list never breaks the session: spawn and tool calls still go through', async ($, on) => {
  const w = world(on)
  w.isListFailing = true
  const spawned = await $.agent.spawn(spawnInput('Look', 'Explore'))
  expect(spawned).toEqual({ model: 'sonnet', agentId: 'a1' })
  const result = await $.tool.call(agentCall('a1', 'Read'))
  expect(result).toMatchObject({ result: 'ok' })
  expect(w.logs.some(line => line.includes('agent-status'))).toBe(true)
})

test('an agent that drops off the list after being seen counts as ended, with one toast', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))
  w.listed = []
  await w.clock.advance(2000)
  expect(w.toasts).toHaveLength(1)
  // The list gave no end status: the row and toast say "ended", not "completed".
  expect(w.toasts[0]).toContain('Explore ended: One')
  await w.clock.advance(30_000)
  expect(w.toasts).toHaveLength(1)
})

// Another mod's band (usage-meter draws one). Each register is self-contained.
// Beneath ours it answers without the engine's band, so ours must call next to keep it.
const bandBeneath: Plugin = {
  name: 'other-band',
  tier: 'append',
  register(on) {
    on('ui.render', { component: 'AbovePrompt' }, ($, e) => {
      const { Box, Text } = $.ui.resolve(e)
      return h(Box, { flexDirection: 'column' }, h(Text, {}, 'other band')) as RenderElement
    })
  },
}

// Above ours it calls next and draws what comes back under its own line.
const bandAbove: Plugin = {
  name: 'other-band',
  tier: 'prepend',
  register(on) {
    on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
      const { Box, Text } = $.ui.resolve(e)
      const below = await next(e)
      return h(Box, { flexDirection: 'column' }, h(Text, {}, 'other band'), below) as RenderElement
    })
  },
}

for (const [where, other] of [['beneath', bandBeneath], ['above', bandAbove]] as const) {
  test(`another AbovePrompt hook ${where} ours is drawn beside the agent rows`, { plugins: [other] }, async ($, on) => {
    const w = world(on)
    w.listed = [running('a1', 'Explore', 'Find auth code')]
    await $.agent.spawn(spawnInput('Find auth code', 'Explore'))

    for (const surface of SURFACES) {
      const ui = await $.ui.mount({ plugin: 'agent-status', surface, component: 'AbovePrompt', props: BAND })
      expect((await ui.find({ key: 'agent-a1' }))?.text).toContain('Explore')
      expect(await ui.find({ type: 'Text', text: 'other band' })).toBeDefined()
      await ui.unmount()
    }
  })
}

test('session.start can fire again (hot reload): no second toast, command re-registered', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))
  w.listed = [{ ...running('a1', 'Explore', 'One'), status: 'completed' }]
  const start = { cwd: '/tmp', surface: 'terminal', isInteractive: true } as const
  await $.session.start(start)
  await $.session.start(start)
  expect(w.toasts).toHaveLength(1)
  expect(w.commands).toEqual(['agent-status', 'agent-status'])
})

const bandOf = ($: Engine, props = BAND) =>
  $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'AbovePrompt', props })

test('tool calls and turns of an unlisted agent (engine fork) make no row and no timer', async ($, on) => {
  const w = world(on)
  await $.tool.call(agentCall('fork-1', 'Read'))
  await $.turn.complete(turnInput('fork-1', [500, 500, 0, 0]))
  await $.tool.call(agentCall('fork-1', 'Grep'))
  const calls = w.listCalls

  await w.clock.advance(60_000)
  expect(w.listCalls).toBe(calls) // no timer running

  const ui = await bandOf($)
  expect(await ui.find({ key: 'agent-fork-1' })).toBeUndefined()
  expect(await ui.find({ text: 'engine band' })).toBeDefined()
  const pane = await $.ui.mount({ plugin: 'agent-status', surface: 'terminal', component: 'Pane', requestId: 'agent-status', props: PANE })
  expect(await pane.find({ key: 'row-fork-1' })).toBeUndefined()
})

test('a spawned agent the list never names is dropped after a few refreshes and stops the timer', async ($, on) => {
  const w = world(on)
  w.nextAgent = 'wf-1'
  await $.agent.spawn(spawnInput('Workflow step', 'general-purpose'))
  const ui = await bandOf($)
  expect(await ui.find({ key: 'agent-wf-1' })).toBeDefined() // shown while it may still be listed

  await w.clock.advance(20_000)
  const calls = w.listCalls
  await w.clock.advance(60_000)
  expect(w.listCalls).toBe(calls)

  const after = await bandOf($)
  expect(await after.find({ key: 'agent-wf-1' })).toBeUndefined()
  expect(await after.find({ text: 'engine band' })).toBeDefined()
  expect(w.toasts).toEqual([])
})

test('a failed tick stops the timer; the next event starts a fresh one', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One')]
  await $.agent.spawn(spawnInput('One', 'Explore'))
  const before = w.listCalls

  w.isListFailing = true
  await w.clock.advance(2000)
  expect(w.listCalls).toBe(before + 1)
  expect(w.logs.some(line => line.includes('tick'))).toBe(true)
  await w.clock.advance(20_000)
  expect(w.listCalls).toBe(before + 1) // stopped after the failure

  w.isListFailing = false
  await $.tool.call(agentCall('a1', 'Read')) // a1 is still tracked and active
  await w.clock.advance(2000)
  expect(w.listCalls).toBeGreaterThan(before + 1)
})

test('an ended row stays 10 s while another agent keeps the clock ticking, then goes', async ($, on) => {
  const w = world(on)
  w.listed = [running('a1', 'Explore', 'One'), running('a2', 'Plan', 'Two')]
  w.nextAgent = 'a1'
  await $.agent.spawn(spawnInput('One', 'Explore'))
  w.nextAgent = 'a2'
  await $.agent.spawn(spawnInput('Two', 'Plan'))
  w.listed = [{ ...running('a1', 'Explore', 'One'), status: 'completed' }, running('a2', 'Plan', 'Two')]
  await w.clock.advance(2000)

  await w.clock.advance(6000) // 6 s after a1 ended
  const mid = await bandOf($)
  expect(await mid.find({ key: 'agent-a1' })).toBeDefined()
  await mid.unmount()

  await w.clock.advance(6000) // 12 s after
  const late = await bandOf($)
  expect(await late.find({ key: 'agent-a1' })).toBeUndefined()
  expect(await late.find({ key: 'agent-a2' })).toBeDefined()
})

test('the band takes no more rows than maxRows leaves room for', async ($, on) => {
  const w = world(on)
  w.listed = Array.from({ length: 5 }, (_, i) => running(`a${i + 1}`, 'general-purpose', `Task ${i + 1}`))
  for (const l of w.listed) {
    w.nextAgent = l.id
    await $.agent.spawn(spawnInput(l.description, l.type))
  }
  const ui = await bandOf($, { ...BAND, maxRows: 3 })
  expect(await ui.find({ key: 'agent-a1' })).toBeDefined()
  expect(await ui.find({ key: 'agent-a2' })).toBeDefined()
  expect(await ui.find({ key: 'agent-a3' })).toBeUndefined()
  expect((await ui.find({ type: 'Text', text: /more$/ }))?.text).toBe('+3 more')
})

test('a listed agent with no spawn (a forked skill) shows on its first tool call; a fork costs one list call', async ($, on) => {
  const w = world(on)
  w.listed = [running('sk-1', 'Explore', 'Forked skill')]
  await $.tool.call(agentCall('sk-1', 'Read'))
  const ui = await bandOf($)
  expect(await ui.find({ key: 'agent-sk-1' })).toBeDefined()

  const before = w.listCalls
  await $.tool.call(agentCall('fork-2', 'Read'))
  await $.tool.call(agentCall('fork-2', 'Grep'))
  expect(w.listCalls).toBe(before + 1)
})
