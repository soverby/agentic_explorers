# modal

[Modal](https://modal.com) is an AI infrastructure platform for inference, batch jobs, training, and sandboxes, driven from Python.

## Install

Create an account at [modal.com](https://modal.com). Then, on macOS or Linux, with Python:

```bash
pip install modal
modal setup          # or: python -m modal setup
```

`modal setup` authenticates the CLI. [Source](https://modal.com/docs/guide).

## What goes here

Modal apps that only run on Modal:

- **Training:** fine-tuning and training jobs on Modal GPUs.
- **Inference:** model-serving endpoints.
- **Sandboxes:** [Modal Sandboxes](https://modal.com/docs/guide/sandboxes), secure containers that run untrusted user or agent code.

Put platform-neutral training recipes, dataset cards, and evals in [`training/`](../../training/). A Modal app here can wrap a recipe from there.

## Run a Modal app

```bash
modal run vendors/modal/<name>/app.py                  # run once
modal run vendors/modal/<name>/app.py::<function>      # run one function
modal deploy vendors/modal/<name>/app.py               # deploy and keep it running
```

References: [`modal run`](https://modal.com/docs/reference/cli/run), [`modal deploy`](https://modal.com/docs/reference/cli/deploy).

This repo has no agent-loader for Modal. Skills, plugins, and agents run in an agent (see the other `vendors/` READMEs). A Modal app can host the model or sandbox that the agent uses.

## Contents

- `<name>/app.py` with a README: what it does, GPU type, cost, tested-with
- Never commit weights, datasets, or tokens. Use Modal volumes and secrets, and link to them.
