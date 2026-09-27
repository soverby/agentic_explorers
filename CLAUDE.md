@AGENTS.md

## Claude Code specifics

- `.claude-plugin/marketplace.json` makes this repo a Claude Code plugin marketplace: `/plugin marketplace add <repo-url>`.
- Each plugin in `plugins/` that supports Claude Code has `.claude-plugin/plugin.json`. Its `agents`, `commands`, `hooks`, and `mcpServers` paths point into the plugin's `com.anthropic.claude-code/` directory. `agents` must be a list of `.md` file paths (a directory path fails validation). `skills/` is shared with the portable format.
- Validate before commit: `claude plugin validate plugins/<name>` and `claude plugin validate .`
