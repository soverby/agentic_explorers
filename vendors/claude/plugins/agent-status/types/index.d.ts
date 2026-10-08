export type AgentStatus =
  | 'pending'
  | 'running'
  | 'waiting'
  | 'idle'
  | 'completed'
  | 'failed'
  | 'killed'
  // Seen in the list, then gone from it: the engine dropped its task.
  | 'ended'

export type AgentCall = { id: string; tool: string }

export type AgentTokens = {
  input: number
  output: number
  cacheRead: number
  cacheWrite: number
}

export type AgentRow = {
  id: string
  type: string
  description: string
  status: AgentStatus
  parentId: string | null
  startedAt: number
  endedAt: number | null
  toolCount: number
  calls: AgentCall[]
  tokens: AgentTokens
  isSeen: boolean
  // Refreshes that did not list a spawned agent; too many and the row is dropped.
  misses: number
  isNotified: boolean
}

declare module 'claude-code' {
  interface PluginState {
    'agent-status': {
      agents: Record<string, AgentRow>
      now: number
    }
  }
}
