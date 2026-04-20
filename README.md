# CYOA TUI

AI-generated choose-your-own-adventure fiction in the terminal, built with `Textual`.

![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Textual](https://img.shields.io/badge/UI-Textual-2f6fed)
![llama.cpp](https://img.shields.io/badge/Local%20LLM-llama.cpp-6b8e23)
![Coverage](https://raw.githubusercontent.com/KishanPipariya/CYOA_TUI/main/.github/badges/coverage.svg)
![License](https://img.shields.io/badge/license-MIT-green)

`python` `textual` `llama.cpp` `gguf` `terminal-ui` `choose-your-own-adventure` `local-llm`

## Why This Project

Many terminal LLM demos stop at "generate text and print it." This project aims higher: streaming story turns, structured choices, save/load state, bookmarks, export, optional memory retrieval, optional graph persistence, and observable runtime behavior.

It is also easy to demo. `mock` mode gives you a deterministic local showcase path, while the same app can run against `llama.cpp` when you want real model-backed behavior.

## Overview

This project turns your terminal into a story engine. It streams narrative text, presents branching choices, and supports local GGUF models through `llama.cpp`.

The app can also persist story structure to Neo4j, keep lightweight memory with Chroma, and export observability data through OpenTelemetry. Those integrations are optional at runtime.

## Features

- Terminal-first adventure UI built with `Textual`
- Streaming story generation with branching choices
- Local `llama.cpp` support for GGUF models
- `mock` provider for development and smoke testing
- Theme-based story starts plus direct prompt overrides
- Save/load support, undo/redo, bookmarks, and story export
- Journal, story map, help screen, typewriter controls, and runtime preset cycling
- Editable story directives during a run
- Optional Neo4j persistence and observability stack

## Showcase Highlights

- `mock` mode for a no-model-required demo path
- local GGUF support through `llama.cpp`
- provider-agnostic runtime with generation presets
- undo/redo, bookmarks, branch rewind, and story export
- graceful degradation when optional services are offline
- strong local quality signals with tests, typing, linting, and coverage floors

## Quick Start

### Requirements

- Python `3.13+`
- `uv`
- One of:
  - a GGUF model for `llama_cpp`
  - `LLM_PROVIDER=mock` for local development

### Install

```bash
uv sync
cp .env.example .env
```

### Configure

Set the provider in `.env`:

```env
LLM_PROVIDER=llama_cpp
LLM_MODEL_PATH=/path/to/model.gguf
```

Supported providers:

- `llama_cpp`
- `mock`

### Run

```bash
uv run python main.py
```

Fastest local demo path:

```bash
LLM_PROVIDER=mock uv run python main.py --theme dark_dungeon
```

Preset-driven demo path:

```bash
LLM_PROVIDER=mock uv run python main.py --runtime-preset mock-smoke
```

Use a built-in theme:

```bash
uv run python main.py --theme space_explorer
```

Other built-in themes include `dark_dungeon`, `haunted_observatory`, `neon_heist`, and `sunken_realm`.

Override the opening prompt directly:

```bash
LLM_PROVIDER=mock uv run python main.py --prompt "Start in a haunted observatory."
```

## Demo Snapshot

![CYOA TUI live screenshot](docs/assets/Screenshot%202026-04-18%20at%205.49.19%E2%80%AFPM.png)

Current README placeholder image stored in `docs/assets`.

The live app also includes a journal, story map, typewriter narration, save/load, bookmarks, export, and optional observability hooks.

## Architecture At A Glance

```text
main.py
  -> validates startup config and selects the prompt/runtime profile
  -> initializes observability and story logging
  -> launches CYOAApp

CYOAApp
  -> renders the Textual interface and handles interaction flows
  -> delegates turn orchestration to StoryEngine

StoryEngine
  -> manages game state, summaries, retries, branching, and persistence hooks
  -> uses ModelBroker for provider-agnostic generation

ModelBroker / Providers
  -> support llama.cpp and mock backends
```

## Controls

| Key | Action |
| :--- | :--- |
| `up` / `down` | Move between choices |
| `enter` | Confirm focused choice |
| `1-4` | Select a choice |
| `space` | Skip current typewriter animation |
| `t` | Toggle typewriter |
| `v` | Cycle typewriter speed |
| `g` | Cycle generation preset |
| `u` | Undo last choice |
| `y` | Redo last choice |
| `b` | Branch from a past scene |
| `j` | Toggle journal |
| `m` | Toggle story map |
| `k` / `p` | Create / restore bookmark |
| `d` | Toggle dark/light theme |
| `x` | Edit story directives |
| `h` | Show help |
| `e` | Export story |
| `s` / `l` | Save / load game |
| `r` | Restart |
| `q` | Quit |

## Optional Services

Bring up the bundled local stack if you want graph persistence and telemetry:

```bash
docker-compose up -d
```

Included services:

- Neo4j
- Jaeger v2
- OpenTelemetry Collector
- Prometheus
- Grafana

The compose stack uses the Jaeger v2 image because Jaeger v1 reached end-of-life on December 31, 2025.

If Neo4j or Chroma are unavailable, the app falls back gracefully instead of failing hard.

## Development

```bash
uv sync --group dev
uv run python scripts/validate_themes.py
bash scripts/run_smoke.sh
uv run pytest -q
uv run ruff check .
uv run mypy cyoa
```

## Quality Summary

Run the local gates in this order before pushing:

```bash
bash scripts/run_smoke.sh
uv run pytest --cov=cyoa --cov-report=term-missing --cov-report=json -q
uv run python scripts/check_coverage.py
uv run ruff check .
uv run mypy cyoa
uv run mypy .  # optional full-repo check
```

This matches the staged CI flow: catch startup regressions quickly, collect coverage before enforcing package floors, then run style and typing gates.

## Current Quality Snapshot

- Full test suite: `296 passed`
- Total coverage in this workspace: `89.46%`
- Enforced package floors:
  - `cyoa/core` `94.41%` against `83%`
  - `cyoa/llm` `94.38%` against `78%`
  - `cyoa/db` `94.09%` against `72%`
  - `cyoa/ui` `89.69%` against `85%`

## Project Structure

```text
cyoa/      Core engine, LLM integration, DB helpers, and UI
themes/    Theme definitions and story starters
tests/     Test suite
monitoring/Telemetry and Grafana/Prometheus config
main.py    CLI entrypoint
```

## Further Reading

- [CODEWIKI.md](CODEWIKI.md) for a code-oriented walkthrough
- [workflow.md](workflow.md) for the original interaction flow
- [loading_art.md](loading_art.md) for loading screen content

## License

[MIT](LICENSE)
