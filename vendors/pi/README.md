# pi

[pi](https://pi.dev) is a minimal, extensible coding agent for the terminal, started by Mario Zechner. Source: [earendil-works/pi](https://github.com/earendil-works/pi).

## Install

macOS or Linux, with npm (Node.js 22.19 or later):

```bash
npm install -g --ignore-scripts @earendil-works/pi-coding-agent
```

The old package `@mariozechner/pi-coding-agent` is deprecated. The official installer is in the [pi README](https://github.com/earendil-works/pi/tree/main/packages/coding-agent). Start pi in your project with `pi`, then run `/login` to connect a provider.

## Use this repo's content with pi

pi keeps user files in `~/.pi/agent/` and project files in `.pi/`. Project files load only after you trust the project. [Source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/configuration.md).

**Skills.** pi implements the Agent Skills spec. It reads skills from `~/.pi/agent/skills/` and `.pi/skills/`, and also from `~/.agents/skills/` and `.agents/skills/`. [Source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md).

```bash
cp -R skills/<name> ~/.pi/agent/skills/<name>
pi --skill ./skills/<name>                        # one session only
pi --skill ./plugins/<name>/skills/<skill>        # a skill from a plugin
```

**Plugins.** pi has no Agent Plugins loader in its docs. Load the skills inside `plugins/<name>/skills/` as shown above. pi's own format is the pi package (`pi install npm:...`, `git:...`, or a local path). [Source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/packages.md).

**Extensions.** Extensions are TypeScript modules. pi reads them from `~/.pi/agent/extensions/` and `.pi/extensions/`. Load one for a session with `pi -e <path>`. [Source](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/extensions.md).

**AGENTS.md and agents.** pi loads `AGENTS.md` or `CLAUDE.md` from `~/.pi/agent/`, the working directory, and each parent directory. So this repo's `AGENTS.md` loads when you run pi in a clone. pi docs show no subagent format. To use an agent from `agents/<name>/`, add its prompt to the system prompt:

```bash
pi --append-system-prompt agents/<name>/AGENT.md
```

## Contents

- pi extensions (`.ts`) and pi packages that only work in pi
- `settings.json` and `models.json` examples
