# ollama

[Ollama](https://ollama.com) runs open-weight language models on your own machine.

## Install

macOS (14 Sonoma or later): download the app from [ollama.com/download](https://ollama.com/download) and drag it to `Applications`.

Linux:

```bash
# Official installer. scan-allow: pipe-to-shell
curl -fsSL https://ollama.com/install.sh | sh
```

Run `ollama --version` to check the install. [Linux docs](https://docs.ollama.com/linux), [macOS docs](https://docs.ollama.com/macos).

## Pull and run models

```bash
ollama pull <model>          # download a model
ollama run <model>           # chat with it
ollama list                  # show local models
```

## Modelfiles in this directory

A [Modelfile](https://docs.ollama.com/modelfile) builds a custom model from a base model:

```text
FROM <base-model>
PARAMETER num_ctx 65536
SYSTEM You are a careful coding assistant.
```

```bash
ollama create <name> -f vendors/ollama/<name>/Modelfile
ollama run <name>
```

## Use a local model with coding agents

`ollama launch <integration>` configures a supported agent to use Ollama and starts it. Supported integrations include `claude`, `codex`, and `pi` (see `ollama launch --help`). Ollama recommends a context window of at least 64k tokens for Codex, and 64k or more for Claude Code on large repos.

- **Claude Code:** `ollama launch claude`. Manual setup: set `ANTHROPIC_AUTH_TOKEN=ollama`, `ANTHROPIC_BASE_URL=http://localhost:11434`, `ANTHROPIC_API_KEY=""`, then run `claude --model <model>`. [Source](https://docs.ollama.com/integrations/claude-code).
- **Codex:** `ollama launch codex`, or `codex --oss -m <model>`. [Source](https://docs.ollama.com/integrations/codex).
- **pi:** `ollama launch pi`, or add an `ollama` provider to `~/.pi/agent/models.json` with `"baseUrl": "http://localhost:11434/v1"` and `"api": "openai-completions"`, and `"apiKey": "ollama"`. [Source](https://docs.ollama.com/integrations/pi).

Skills and agents from this repo load the same way as with a hosted model. See the README for each agent in `vendors/`.

## Contents

- `<name>/Modelfile` with a README: base model, parameters, tested-with
- local-model setup notes
