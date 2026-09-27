# Training

Model training, fine-tuning, and evaluation work. Keep it platform-neutral where possible. Put platform glue (Modal apps, Ollama Modelfiles) in `vendors/`, and link to it.

- `recipes/<name>/` — a training or fine-tuning run: config, scripts, README with base model, hardware, cost, and results
- `datasets/<name>/` — dataset cards only (source, license, schema, link). Do not commit data.
- `evals/<name>/` — eval harnesses and result tables

Weights and checkpoints go to Hugging Face, Modal volumes, or releases. `.gitignore` blocks common weight formats.
