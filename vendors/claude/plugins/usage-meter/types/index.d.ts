export type UsageMeterWindow = {
  kind: string
  percentUsed: number
  resetsAt?: string
}

declare module 'claude-code' {
  interface PluginState {
    'usage-meter': {
      // Last reading of the account windows.
      windows: UsageMeterWindow[]
      // Clock reading (ms) the countdowns are drawn against; moves with the 60 s timer.
      nowMs: number
      // Highest toast level sent per `kind@resetsAt`: 1 warn, 2 critical.
      fired: Record<string, number>
    }
  }
}
