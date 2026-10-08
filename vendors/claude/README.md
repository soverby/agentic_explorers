# claude

[Claude Code](https://code.claude.com/docs/en/overview) is Anthropic's agentic coding tool for the terminal, IDEs, and desktop.

## Install

| OS | Command |
| --- | --- |
| macOS | `brew install --cask claude-code` |
| Linux | apt, dnf, or apk package: see the [setup guide](https://code.claude.com/docs/en/setup#install-with-linux-package-managers) |
| Windows | `winget install Anthropic.ClaudeCode` (PowerShell or CMD) |
| Any, with npm (Node.js 22+) | `npm install -g @anthropic-ai/claude-code` |

Windows runs natively, or inside WSL. On native Windows, [Git for Windows](https://git-scm.com/downloads/win) is optional: with it, Claude Code uses Git Bash; without it, PowerShell. Sandboxing needs WSL 2. The native installers are in the [setup guide](https://code.claude.com/docs/en/setup). Run `claude --version` to check the install.

On Windows, `~/.claude` means `%USERPROFILE%\.claude` ([source](https://code.claude.com/docs/en/settings)). So the paths below become `%USERPROFILE%\.claude\skills\<name>\` and `%USERPROFILE%\.claude\agents\`.

## Use this repo's content with Claude Code

**Plugins.** In a Claude Code session, add this repo as a marketplace, then install a plugin:

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install <name>@agentic-explorers
```

To test a plugin from a local clone for one session: `claude --plugin-dir ./plugins/<name>`.

**Standalone skills.** Claude Code reads skills from `~/.claude/skills/<name>/SKILL.md` (all your projects) and `.claude/skills/<name>/SKILL.md` (one project). Copy the skill directory there:

```bash
cp -R skills/<name> ~/.claude/skills/<name>
```

```powershell
Copy-Item -Recurse skills\<name> "$env:USERPROFILE\.claude\skills\<name>"
```

**Agents.** Claude Code reads subagents from `~/.claude/agents/` (user) and `.claude/agents/` (project). Copy the Claude variant of an agent:

```bash
cp agents/<name>/vendors/claude.md ~/.claude/agents/<name>.md
```

```powershell
Copy-Item agents\<name>\vendors\claude.md "$env:USERPROFILE\.claude\agents\<name>.md"
```

**Claude-only plugins** live in `vendors/claude/plugins/<name>/`:

- `usage-review`: weekly token usage by model and effort, with recommendations.
- `agent-status`: live sub-agent status band above the prompt, `/agent-status` pane (a mod).
- `usage-meter`: account rate-limit windows above the prompt, `/limits` (a mod).
- `context-meter`: context fill, last-turn tokens and cost in the status line, `/ctx` (a mod).

Other Anthropic surfaces: the [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) runs the Claude Code agent loop from Python or TypeScript. The [Claude API](https://platform.claude.com/docs/en/api/overview) gives direct model access.

## Contents

- `plugins/<name>/`: plugins that only work in Claude Code
- settings, hooks, and output styles
- Agent SDK and API examples
