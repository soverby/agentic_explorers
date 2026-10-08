export type ContextTokens = {
  input: number
  output: number
  cacheRead: number
  cacheCreation: number
}

export type ContextTotals = ContextTokens & { turns: number }

declare module 'claude-code' {
  interface PluginState {
    'context-meter': {
      lastTurn: ContextTokens | null
      totals: ContextTotals
      isWarned: boolean
    }
  }
}
