# codex

[Codex CLI](https://github.com/openai/codex) is OpenAI's coding agent for the terminal.

## Install

| OS | Command |
| --- | --- |
| macOS | `brew install --cask codex` or `npm install -g @openai/codex` |
| Linux | `npm install -g @openai/codex` |
| Windows | `npm install -g @openai/codex` (npm ships `win32-x64` and `win32-arm64` builds) |

Then run `codex` and sign in. Standalone installers for each OS are in the [Codex README](https://github.com/openai/codex).

Windows: the CLI runs natively, with a Windows sandbox. WSL is optional, for Linux-native tooling. [Source](https://learn.chatgpt.com/docs/windows/windows-sandbox). The docs write paths as `~/.codex` and `~/.agents`. The Windows form (`%USERPROFILE%\.codex`, `%USERPROFILE%\.agents`) is Unverified.

## Use this repo's content with Codex

**AGENTS.md.** Codex reads `AGENTS.md` (or `AGENTS.override.md`) from `~/.codex/` first. Then it reads one file in each directory from the Git root down to the current directory. Files closer to your working directory come later, so they win. The total is capped at 32 KiB by default (`project_doc_max_bytes`). This repo's root `AGENTS.md` loads when you run Codex inside a clone. [Source](https://learn.chatgpt.com/docs/agent-configuration/agents-md).

**Skills.** Codex supports Agent Skills (`SKILL.md` with `name` and `description`). It reads them from `.agents/skills/` in the current directory, its parents, and the repo root, and from `~/.agents/skills/` for the user. [Source](https://learn.chatgpt.com/docs/build-skills).

```bash
cp -R skills/<name> ~/.agents/skills/<name>
```

**Plugins.** Codex supports plugins with a root `plugin.json` (the Agent Plugins format). Codex-only data goes under `extensions.com.openai`. Register a marketplace source with `codex plugin marketplace add <owner/repo>`, then browse and install with `/plugins` in the CLI. The ChatGPT desktop app reads marketplace files from `.agents/plugins/marketplace.json` and, as a legacy-compatible path, `.claude-plugin/marketplace.json`. Sources: [build plugins](https://developers.openai.com/plugins/build/plugins), [use plugins](https://learn.chatgpt.com/docs/plugins).

```bash
codex plugin marketplace add soverby/agentic_explorers
```

Unverified: that the Codex CLI lists this repo's plugins from `.claude-plugin/marketplace.json`. If it does not, copy `plugins/<name>/skills/<skill>/` into `~/.agents/skills/`.

**Agents.** Codex reads custom agents as TOML files from `~/.codex/agents/` (user) and `.codex/agents/` (project). Each file needs `name`, `description`, and `developer_instructions`. Put the text of `agents/<name>/AGENT.md` in `developer_instructions`. [Source](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Contents

- `config.toml` examples and profiles
- prompts and custom agent TOML files that only work in Codex
