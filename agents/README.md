# Agents

Agent and subagent definitions: roles, system prompts, delegation patterns.

```
agents/<name>/
├── AGENT.md           portable definition (from templates/agent/example-agent/)
├── README.md          purpose, tested-with, author
└── vendors/           optional native formats
    ├── claude.md      Claude Code subagent (.claude/agents/ format)
    ├── codex.md       Codex adaptation
    └── ...
```

The portable `AGENT.md` is the source of truth. Vendor files are adaptations of it.
