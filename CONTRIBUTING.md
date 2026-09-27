# Contributing

1. Pick the location from the table in [AGENTS.md](AGENTS.md).
2. Copy the matching directory from `templates/`.
3. Fill in the `README.md`: what it does, which agents/models you tested it with, and your name or handle.
4. Run `python3 scripts/ci/scan_content.py --all`.
5. Open a pull request from your fork. One contribution per PR. Only the maintainer (@soverby) merges.

By contributing, you agree to license your contribution under the repo's [MIT License](LICENSE). Full walkthrough: [README.md](README.md#contribute).

## Checklist

- [ ] Directory name and manifest `name` match (lowercase, digits, hyphens).
- [ ] Skills: `SKILL.md` has `name` and `description` frontmatter; body under 500 lines.
- [ ] Plugins: `plugin.json` has `$schema` and `name`; only spec fields at top level; vendor data under `extensions` or a reverse-domain directory.
- [ ] Plugins for Claude Code: entry added to `.claude-plugin/marketplace.json`.
- [ ] No secrets, tokens, `.env` files, model weights, or large datasets.
- [ ] No phone numbers, chat exports, or personal data about other people.
- [ ] No binaries or archives.
- [ ] Tested-with list in the README (agent + model + version).

## Specs

- Agent Plugins: https://agent-plugins.org/specification
- Agent Skills: https://agentskills.io/specification
- Claude Code plugins: https://code.claude.com/docs/en/plugins
