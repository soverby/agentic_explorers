# Vendors

Material that only applies to one agent, runtime, or platform. If the content can be portable, put it in `plugins/`, `skills/`, or `agents/`.

| Directory | Vendor | Typical content |
|---|---|---|
| `claude/` | Anthropic Claude Code / Agent SDK / API | settings, hooks, output styles, SDK examples |
| `codex/` | OpenAI Codex | config.toml, profiles, prompts |
| `pi/` | pi coding agent | extensions, config |
| `ollama/` | Ollama | Modelfiles, local-model setup |
| `modal/` | Modal Labs | Modal apps for training, inference, sandboxes |

To add a vendor, create `vendors/<vendor>/README.md`: what the vendor is, install notes, and how to load this repo's skills/plugins in it.
