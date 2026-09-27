# Agent instructions

Vendor-neutral instructions for any coding agent (Claude Code, Codex, pi, others) that works in this repository.

## What this repo is

A shared library for the Agentic Engineering community: skills, agent definitions, plugins, docs, and training recipes. Content is agent-agnostic by default. Vendor-specific content lives in clearly marked places.

## Where content goes

| Content | Location | Format |
|---|---|---|
| Portable plugin | `plugins/<name>/` | [Agent Plugins 1.0.0](https://agent-plugins.org/specification) |
| Standalone skill | `skills/<name>/` | [Agent Skills](https://agentskills.io/specification) |
| Agent / subagent definition | `agents/<name>/` | Markdown + optional vendor variants |
| Guides, write-ups, research notes | `docs/` | Markdown |
| Model training / fine-tuning / evals | `training/` | Recipes, dataset cards, eval harnesses |
| Vendor-only material | `vendors/<vendor>/` | Vendor-native |
| Starting points | `templates/` | Copy, do not edit in place |

## Rules

- Put vendor-specific files inside a plugin under a reverse-domain directory (for example `com.anthropic.claude-code/`). Never add vendor fields to the top level of `plugin.json`; use its `extensions` object. Exception: `.claude-plugin/plugin.json` (Claude Code requires it there; keep that directory to the manifest only).
- A skill `name` must match its directory name: lowercase, digits, single hyphens, max 64 chars.
- Never commit secrets, API keys, model weights, or large datasets. Link to them.
- When you add or rename a plugin, update `.claude-plugin/marketplace.json`.
- Keep each contribution self-contained with its own `README.md` that names the author.
