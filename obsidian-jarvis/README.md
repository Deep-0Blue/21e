# Obsidian Jarvis

> Turn a 3AM idea into a finished, shipped project — while you sleep.

Inspired by [this post](https://x.com/undefinedKi/status/2063305573097951631): wire
your Obsidian vault into a "Jarvis" pipeline that takes a raw idea note and carries
it all the way to a finished project, automatically.

You have way more ideas than time to build them. So drop a brain-dump into your
vault's inbox, leave Jarvis running overnight, and wake up to a refined spec, a
build plan, **and a generated, runnable project**.

## How it works

Each idea note flows through an ordered, resumable pipeline. The note's
frontmatter `status` records progress, so a restart just picks up where it left off.

```
idea  ──▶  refined  ──▶  planned  ──▶  built  ──▶  shipped
 │           │             │            │            │
 capture     spec +        milestones   generated    release notes,
 in inbox    features      + tasks       project      changelog, next steps
```

| Stage   | What Jarvis does                                                        |
|---------|------------------------------------------------------------------------|
| refine  | Expands the raw idea into a problem statement, audience, value, features |
| plan    | Produces a stack, milestones, tasks, and a definition of done           |
| build   | Generates a runnable starter project under `10-Projects/<slug>/`        |
| ship    | Writes release notes + a `CHANGELOG.md` and marks the note shipped      |

Everything is written **back into the note**, so your vault becomes a living log of
how each idea evolved.

## Quick start

No dependencies are required for the default offline engine.

```bash
cd obsidian-jarvis

# 1. Create the vault folders and an example idea
python -m jarvis --vault ./vault init

# 2. Process every pending idea once
python -m jarvis --vault ./vault run

# 3. See where each idea stands
python -m jarvis --vault ./vault status
```

Capture a new idea straight from the terminal:

```bash
python -m jarvis --vault ./vault capture "A CLI that turns receipts into a budget" --run
```

Run it overnight (watches the inbox and processes anything new):

```bash
python -m jarvis --vault ./vault watch --interval 30
```

## Pointing it at your real vault

```bash
python -m jarvis --vault "~/Obsidian/MyVault" watch
```

Or use a config file (see `config.example.yaml`):

```bash
cp config.example.yaml config.yaml   # edit vault path, etc.
python -m jarvis --config config.yaml watch
```

## Using a real LLM (optional)

The offline engine is deterministic and needs no network — perfect for trying it
out and for CI. To get richer output, point Jarvis at any OpenAI-compatible
endpoint (OpenAI, Azure, Ollama, LM Studio, OpenRouter, …):

```bash
export JARVIS_LLM_API_KEY=sk-...
python -m jarvis --vault ./vault run --backend openai --model gpt-4o-mini
```

Configurable via env vars: `JARVIS_VAULT`, `JARVIS_INBOX`, `JARVIS_PROJECTS`,
`JARVIS_LLM_BACKEND`, `JARVIS_LLM_MODEL`, `JARVIS_LLM_BASE_URL`,
`JARVIS_LLM_API_KEY` (or `OPENAI_API_KEY`).

## Idea note format

Any markdown file in the inbox works. Optional frontmatter controls the pipeline:

```markdown
---
status: idea          # idea | refined | planned | built | shipped
jarvis_ignore: false  # set true to skip a note
---

# Voice-to-task inbox

Ramble a voice memo -> transcribe -> split into tasks -> tag by project.
```

## Run the tests

```bash
cd obsidian-jarvis
python -m pytest
```

## Project layout

```
obsidian-jarvis/
├── jarvis/
│   ├── cli.py        # init / capture / run / watch / status commands
│   ├── config.py     # config from file, env, and CLI
│   ├── note.py       # Obsidian markdown + frontmatter read/write
│   ├── llm.py        # offline engine + OpenAI-compatible backend
│   └── pipeline.py   # the idea -> shipped stages
├── vault/            # example vault (inbox + projects)
├── tests/
├── config.example.yaml
└── requirements.txt
```
