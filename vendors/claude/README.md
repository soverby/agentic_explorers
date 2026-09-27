# claude

[Claude Code](https://code.claude.com/docs/en/overview) is Anthropic's agentic coding tool for the terminal, IDEs, and desktop.

## Install

macOS or Linux, with Homebrew:

```bash
brew install --cask claude-code
```

Or with npm (Node.js 22 or later):

```bash
npm install -g @anthropic-ai/claude-code
```

The native installer and apt, dnf, and apk packages are in the [setup guide](https://code.claude.com/docs/en/setup). Run `claude --version` to check the install.

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

**Agents.** Claude Code reads subagents from `~/.claude/agents/` (user) and `.claude/agents/` (project). Copy the Claude variant of an agent:

```bash
cp agents/<name>/vendors/claude.md ~/.claude/agents/<name>.md
```

**Claude-only plugins** live in `vendors/claude/plugins/<name>/`. The first one, `usage-review`, comes in a separate PR.

Other Anthropic surfaces: the [Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) runs the Claude Code agent loop from Python or TypeScript. The [Claude API](https://platform.claude.com/docs/en/api/overview) gives direct model access.

## Contents

- `plugins/<name>/`: plugins that only work in Claude Code
- settings, hooks, and output styles
- Agent SDK and API examples
