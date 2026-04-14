# CYOA TUI

AI-generated choose-your-own-adventure fiction in the terminal, built with `Textual`.

![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Textual](https://img.shields.io/badge/UI-Textual-2f6fed)
![llama.cpp](https://img.shields.io/badge/Local%20LLM-llama.cpp-6b8e23)
![Ollama](https://img.shields.io/badge/Backend-Ollama-black)
![Coverage](https://raw.githubusercontent.com/KishanPipariya/CYOA_TUI/main/.github/badges/coverage.svg)
![License](https://img.shields.io/badge/license-MIT-green)

`python` `textual` `llama.cpp` `ollama` `gguf` `terminal-ui` `choose-your-own-adventure` `local-llm`

## Overview

This project turns your terminal into a story engine. It streams narrative text, presents branching choices, and supports both local GGUF models through `llama.cpp` and remote/local Ollama models.

The app can also persist story structure to Neo4j, keep lightweight memory with Chroma, and export observability data through OpenTelemetry. Those integrations are optional at runtime.

## Features

- Terminal-first adventure UI built with `Textual`
- Streaming story generation with branching choices
- Local `llama.cpp` support for GGUF models
- `ollama` support for daemon-backed models
- `mock` provider for development and smoke testing
- Theme-based story starts plus direct prompt overrides
- Save/load support and one-step undo
- Journal, story map, help screen, and typewriter mode
- Optional Neo4j persistence and observability stack

## Quick Start

### Requirements

- Python `3.13+`
- `uv`
- One of:
  - a GGUF model for `llama_cpp`
  - a running Ollama instance
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
- `ollama`
- `mock`

### Run

```bash
uv run python main.py
```

Use a built-in theme:

```bash
uv run python main.py --theme space_explorer
```

Override the opening prompt directly:

```bash
LLM_PROVIDER=mock uv run python main.py --prompt "Start in a haunted observatory."
```

## Controls

| Key | Action |
| :--- | :--- |
| `1-4` | Select a choice |
| `space` | Skip current typewriter animation |
| `t` | Toggle typewriter |
| `v` | Cycle typewriter speed |
| `u` | Undo last choice |
| `b` | Branch from a past scene |
| `j` | Toggle journal |
| `m` | Toggle story map |
| `d` | Toggle dark/light theme |
| `h` | Show help |
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
- Jaeger
- OpenTelemetry Collector
- Prometheus
- Grafana

If Neo4j or Chroma are unavailable, the app falls back gracefully instead of failing hard.

## Development

```bash
uv sync --group dev
bash scripts/run_smoke.sh
uv run pytest -q
uv run ruff check .
uv run mypy cyoa
```

## Project Structure

```text
cyoa/      Core engine, LLM integration, DB helpers, and UI
themes/    Theme definitions and story starters
tests/     Test suite
monitoring/Telemetry and Grafana/Prometheus config
main.py    CLI entrypoint
```

## License

[MIT](LICENSE)
