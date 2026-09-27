# Agentic Explorers

A shared library for the **Agentic Engineering** WhatsApp community. Members share the agent tooling they build: skills, agent definitions, plugins, guides, and model-training work.

The repo is **agnostic to vendor, model, and ecosystem**. Content is portable by default, so it can run on Claude Code, Codex, pi, local models through Ollama, and any other agent that supports the open formats. Vendor-specific work has its own clearly marked areas.

- License: [MIT](LICENSE)
- Maintainer: [@soverby](https://github.com/soverby). Only the maintainer can merge.
- Formats: [Agent Plugins 1.0.0](https://agent-plugins.org/specification), [Agent Skills](https://agentskills.io/specification)

---

## Contents

1. [How the repo is organized](#how-the-repo-is-organized)
2. [Portable first, vendor second](#portable-first-vendor-second)
3. [Plugins](#plugins)
4. [Skills, agents, docs, training](#skills-agents-docs-training)
5. [Use content from this repo](#use-content-from-this-repo)
6. [Contribute](#contribute)
7. [Automated security checks](#automated-security-checks)
8. [Review and merge policy](#review-and-merge-policy)
9. [Rules for all content](#rules-for-all-content)

---

## How the repo is organized

```text
.
├── plugins/              Portable plugins (Agent Plugins spec), with optional vendor extensions
├── skills/               Standalone Agent Skills (SKILL.md) for any compatible agent
├── agents/               Agent and subagent definitions, with vendor variants
├── docs/
│   ├── guides/           How-tos and patterns
│   └── research/         Paper notes, experiments, benchmarks
├── training/
│   ├── recipes/          Training and fine-tuning runs
│   ├── datasets/         Dataset cards only (no data)
│   └── evals/            Eval harnesses and results
├── vendors/              Vendor-only material
│   ├── claude/           Anthropic: Claude Code, Agent SDK, API
│   ├── codex/            OpenAI Codex
│   ├── pi/               pi coding agent
│   ├── ollama/           Ollama: Modelfiles, local models
│   └── modal/            Modal Labs: training, inference, sandboxes
├── templates/            Starting points. Copy these; do not edit them in place.
├── scripts/ci/           Security scanner that runs on every pull request
├── .claude-plugin/       Claude Code marketplace index for plugins/
├── .github/              CI workflows, CODEOWNERS, PR template
├── AGENTS.md             Instructions for any coding agent that works in this repo
├── CLAUDE.md             Claude Code entry point (imports AGENTS.md)
└── CONTRIBUTING.md       Contribution checklist
```

Each directory has its own `README.md` with the detailed rules for that area.

## Portable first, vendor second

Use this order to find the right place for your work:

1. **Can it run on more than one agent?** Put it in `plugins/`, `skills/`, or `agents/`, in the open format.
2. **Is it portable, but with some vendor-specific parts?** Keep the portable parts in the open format. Put the vendor parts in a reverse-domain directory inside the plugin, for example `com.anthropic.claude-code/`, as the Agent Plugins spec requires.
3. **Does it apply to one vendor only?** Examples are a Modal app, an Ollama Modelfile, or a Codex config. Put it in `vendors/<vendor>/`.

## Plugins

A plugin is a package of skills, MCP servers, and optional vendor-specific components. This repo uses the [Agent Plugins 1.0.0](https://agent-plugins.org/specification) layout:

```text
plugins/<name>/
├── plugin.json                    REQUIRED  portable manifest ($schema, name, version, ...)
├── README.md                      REQUIRED  what it does, tested-with, author
├── skills/<skill>/SKILL.md        portable  one level deep only
├── mcp.json                       portable  MCP servers (stdio | streamable-http | sse)
│
├── .claude-plugin/plugin.json     Claude Code manifest
└── com.anthropic.claude-code/     Claude Code-only: agents/, commands/, hooks/, mcp.json
```

- The top level of `plugin.json` can contain only the fields that the spec defines. Put vendor data under `extensions.<namespace>`.
- Each vendor directory has a reverse-domain name, for example `com.anthropic.claude-code/`. To add support for another agent, add that agent's namespace directory.
- One exception: Claude Code reads its manifest only from `.claude-plugin/plugin.json`. Keep only that one file in `.claude-plugin/`.

See [plugins/README.md](plugins/README.md) for all rules and the MCP variable differences.

## Skills, agents, docs, training

| Area | What goes there | Format |
| --- | --- | --- |
| `skills/<name>/` | A single skill that needs no packaging | `SKILL.md` with `name` + `description` frontmatter. `name` must match the directory name. |
| `agents/<name>/` | Role prompts, subagent definitions, orchestration patterns | Portable `AGENT.md`, plus optional `vendors/` adaptations |
| `docs/guides/` | How-tos: prompting, orchestration, evals, tooling setup | Markdown. Author and date at the top. |
| `docs/research/` | Paper notes, experiment write-ups, benchmarks | Markdown |
| `training/recipes/<name>/` | A fine-tuning or training run | Config, scripts, and a README with base model, hardware, cost, and results |
| `training/datasets/<name>/` | Dataset **cards** | Source, license, schema, link. Never commit the data. |
| `training/evals/<name>/` | Eval harnesses and results | Code + results table |

Model weights, checkpoints, and datasets go to Hugging Face, Modal volumes, or GitHub releases. Link to them from here.

## Use content from this repo

**Claude Code** — add the repo as a plugin marketplace:

```text
/plugin marketplace add soverby/agentic_explorers
/plugin install <plugin-name>@agentic-explorers
```

**Other agents** (Codex, pi, others) — clone the repo. Then point the agent's skills or plugins path at `skills/<name>/` or `plugins/<name>/`, or copy the directory. Each `vendors/<vendor>/README.md` gives the install steps for that agent.

**Only a skill** — copy `skills/<name>/` into your agent's skills directory.

Read the content before you install it. Skills and plugins can tell an agent to run commands on your machine. CI scans every contribution, but a scan cannot find everything.

## Contribute

All contributions come in as pull requests from a fork. Nobody pushes directly to `main`.

### 1. Fork and clone

```bash
gh repo fork soverby/agentic_explorers --clone
cd agentic_explorers
git checkout -b add-<short-name>
```

No `gh` CLI? Click **Fork** on GitHub, then `git clone` your fork.

### 2. Start from a template

| You are adding | Copy | To |
| --- | --- | --- |
| Plugin | `templates/plugin/example-plugin/` | `plugins/<name>/` |
| Skill | `templates/skill/example-skill/` | `skills/<name>/` |
| Agent | `templates/agent/example-agent/` | `agents/<name>/` |
| Doc, recipe, vendor material | — | the matching directory; add a `README.md` |

After you copy, rename every `name` field to match the new directory name. Names use lowercase letters, digits, and single hyphens, with a maximum of 64 characters.

### 3. Fill in the README

Every contribution has a `README.md` with:

- what it does, in one or two sentences
- a **Tested with** table: agent, version, model
- your name or GitHub handle

### 4. Check locally (recommended)

```bash
# Security and PII scan: the same checks that CI runs
python3 scripts/ci/scan_content.py --all

# Validate a skill
uvx --from 'git+https://github.com/agentskills/agentskills#subdirectory=skills-ref' \
  skills-ref validate ./skills/<name>

# Validate a Claude Code plugin and the marketplace index
claude plugin validate plugins/<name>
claude plugin validate .
```

For a plugin that supports Claude Code, add it to `.claude-plugin/marketplace.json`:

```json
{ "name": "<name>", "source": "./plugins/<name>", "description": "..." }
```

### 5. Open the pull request

```bash
git add <your paths>
git commit -m "Add <name>: <one line>"
git push -u origin add-<short-name>
gh pr create --repo soverby/agentic_explorers --fill
```

Fill in the PR template: a description of the contribution and its type, how to use it, and what you tested it with. Put one contribution in each PR.

### 6. Respond to review

The security checks run automatically. If a check fails, open the check's log. The annotations show the file, the line, and the rule. Fix the problem and push again; the PR updates.

The maintainer can ask for changes. After approval, the maintainer merges.

## Automated security checks

Every pull request and every push to `main` runs [`.github/workflows/security-scan.yml`](.github/workflows/security-scan.yml). A PR cannot merge until all checks pass.

| Check | Tool | Fails on |
| --- | --- | --- |
| `secrets` | [gitleaks](https://github.com/gitleaks/gitleaks) | API keys, tokens, and private keys in any commit of the PR, also when a later commit deleted them |
| `malware` | [ClamAV](https://www.clamav.net/) | Known malware signatures in any file |
| `pii-and-content` | `scripts/ci/scan_content.py` | Phone numbers, WhatsApp chat exports, SSNs, card numbers, IBANs; executables and archives; hidden Unicode (bidi / zero-width characters that can hide prompt injection); reverse shells, `curl … \| sh`, crypto miners; files larger than 5 MB |

The scanner also shows **warnings** (the check does not fail) for email addresses and prompt-injection phrases such as "ignore previous instructions".

**This is a WhatsApp community. Do not paste chat exports, phone numbers, or names of other members without their consent.** The scanner looks for these, but you are responsible for your content.

A false positive can occur, for example an install doc that legitimately contains `curl … | sh`. To suppress it, add `scan-allow: <rule-id>` on the same line or on the line directly above. The maintainer reviews every suppression. [scripts/ci/README.md](scripts/ci/README.md) lists the rule IDs.

## Review and merge policy

- `main` is protected. Nobody can push to it directly, and a pull request is the only way to change it.
- [CODEOWNERS](.github/CODEOWNERS) assigns every path to [@soverby](https://github.com/soverby). **Only the maintainer can merge.** Contributors do not get write access.
- PRs are squash-merged, so only the final, scanned content of a PR goes into `main`.
- To merge, a PR must pass all security checks, be up to date with `main`, and have all review conversations resolved.
- A PR that changes `.github/` or `scripts/ci/` gets extra review. CI runs the PR's own copy of the workflow and scanner, so a PR that weakens the checks could pass them. Put changes of this type in a separate PR.
- The maintainer can close any PR that does not follow these rules, or that looks unsafe, without further discussion.

## Rules for all content

- **No secrets:** no API keys, tokens, `.env` files, or credentials.
- **No personal data:** no phone numbers, chat exports, private emails, or data about other people.
- **No binaries or archives:** commit source. Link to releases for built artifacts.
- **No model weights or datasets:** link to where they are hosted.
- **Only content you have the right to share.** By contributing, you agree to license your contribution under [MIT](LICENSE).
- **Say what you tested.** Name the agent, the model, and the version in each README.
- **No harmful content:** no malware, credential stealers, or skills that exfiltrate data, not even as "demos".

See [CONTRIBUTING.md](CONTRIBUTING.md) for the short checklist, and [AGENTS.md](AGENTS.md) for the instructions that coding agents follow in this repo.
