# Skills

Standalone [Agent Skills](https://agentskills.io/specification). One directory per skill:

```
skills/<name>/
├── SKILL.md       required; frontmatter name == <name>
├── README.md      tested-with + author (not loaded by agents)
├── scripts/       optional
├── references/    optional
└── assets/        optional
```

Use this area for a single skill that needs no packaging. To ship skills with MCP servers, hooks, or agents, make a plugin in `plugins/`.

Validate:

```
uvx --from 'git+https://github.com/agentskills/agentskills#subdirectory=skills-ref' skills-ref validate ./skills/<name>
```
