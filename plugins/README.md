# Plugins

Each plugin is a directory that follows [Agent Plugins 1.0.0](https://agent-plugins.org/specification), with optional vendor extensions.

```
plugins/<name>/
├── plugin.json                    REQUIRED  portable manifest ($schema, name, ...)
├── README.md                      REQUIRED  what, tested-with, author
├── skills/<skill>/SKILL.md        portable  Agent Skills; one level deep only
├── mcp.json                       portable  MCP servers (stdio | streamable-http | sse)
│
├── .claude-plugin/plugin.json     Claude Code manifest; paths point into com.anthropic.claude-code/
└── com.anthropic.claude-code/     Claude Code-only: agents/, commands/, hooks/, mcp.json
    (other vendors: add their own reverse-domain directory, e.g. com.openai.codex/)
```

## Rules

- `plugin.json` top-level fields are limited to: `$schema`, `name`, `version`, `description`, `author`, `homepage`, `repository`, `license`, `keywords`, `extensions`. Put vendor manifest data under `extensions.<namespace>`.
- Vendor directories use reverse-domain names based on the vendor's domain. Use the same namespace in the directory and in `extensions`.
- `skills/` is shared. Every compatible agent reads it, so keep skills portable.
- Claude Code expands `${CLAUDE_PLUGIN_ROOT}`, not `${PLUGIN_ROOT}` / `${PLUGIN_DATA}`. If a server in `mcp.json` uses those variables, keep a Claude copy in `com.anthropic.claude-code/mcp.json`. If not, point `mcpServers` at `./mcp.json`.
- `.claude-plugin/` is a necessary deviation from spec §8 (Claude Code reads its manifest only there). Keep only `plugin.json` in it; put all other Claude files in `com.anthropic.claude-code/`.
- In `.claude-plugin/plugin.json`, `agents` must list each agent `.md` file; a directory path fails `claude plugin validate`.
- Add Claude Code-enabled plugins to `/.claude-plugin/marketplace.json`:
  `{ "name": "<name>", "source": "./plugins/<name>", "description": "..." }`

Start from `templates/plugin/example-plugin/`.
