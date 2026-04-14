# CYOA TUI

AI-generated choose-your-own-adventure fiction in the terminal, built with `Textual`.

The current codebase supports three LLM backends:

- `llama_cpp` for local GGUF models
- `ollama` for an Ollama daemon
- `mock` for fast smoke tests and development

It also includes optional Neo4j graph persistence, in-process Chroma-backed memory with graceful fallback, and OpenTelemetry instrumentation for prompt, latency, and runtime visibility.

## Current Feature Set

- Textual UI with streaming narrative, typewriter mode, help screen, journal, and story map
- Theme-based startup prompts from `themes/*.toml`, plus direct `--prompt` overrides
- Unified generation mode and judge-mode generation, selected with `LLM_UNIFIED_MODE`
- Hierarchical story summarization to keep long sessions within token budget
- Speculative next-node generation and cache reuse
- Neo4j scene persistence with runtime degraded mode when the database is unavailable
- Narrative and NPC memory via Chroma, with recent-history fallback when memory is offline
- Save/load to local JSON files and one-level undo
- OpenTelemetry, Prometheus, Jaeger, and Grafana support

## Repository Layout

- `main.py`: CLI entrypoint, `.env` loading, startup validation, theme selection
- `cyoa/core/`: engine, state, events, models, constants, observability
- `cyoa/llm/`: provider implementations, broker, prompt pipeline, templates
- `cyoa/ui/`: `Textual` app, UI components, mixins, styling, ASCII art
- `cyoa/db/`: Neo4j persistence, story logging, RAG memory wrappers
- `themes/`: theme TOML files and mood-to-style mappings
- `monitoring/`: OTEL collector, Prometheus, Grafana provisioning
- `tests/`: startup, engine, UI, provider, DB, observability, and regression coverage

## Quick Start

### Prerequisites

- Python `3.13+`
- `uv`
- Docker, if you want Neo4j or the observability stack
- Either:
  - a local `.gguf` model for `llama_cpp`
  - a running Ollama instance for `ollama`
  - or `LLM_PROVIDER=mock` for local smoke/dev runs

### Install

```bash
uv sync
```

### Configure

```bash
cp .env.example .env
```

Important notes:

- `LLM_PROVIDER` must be `llama_cpp`, `ollama`, or `mock`
- `llama_cpp` requires `LLM_MODEL_PATH` or `--model`, and the file must exist
- `--prompt` takes precedence over `--theme`
- Available built-in themes are `dark_dungeon` and `space_explorer`

### Optional Infrastructure

```bash
docker-compose up -d
```

This starts:

- Neo4j
- Jaeger
- OpenTelemetry Collector
- Prometheus
- Grafana

Chroma is not a container in this repo. The app uses `chromadb.Client()` in-process and falls back to recent-history memory if Chroma initialization fails.

### Run

Default theme:

```bash
uv run python main.py
```

Specific theme:

```bash
uv run python main.py --theme space_explorer
```

Direct prompt override:

```bash
LLM_PROVIDER=mock uv run python main.py --prompt "Start in a haunted observatory."
```

## Startup Validation

Before the UI starts, `main.py` validates:

- `LLM_PROVIDER`
- `LLM_N_CTX`
- `LLM_MAX_TOKENS`
- `LLM_TOKEN_BUDGET`
- `LLM_TEMPERATURE`
- required local model configuration for `llama_cpp`

Invalid startup configuration exits early with status code `2`.

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

## Configuration Reference

### Provider selection

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `LLM_PROVIDER` | `llama_cpp`, `ollama`, or `mock` | `llama_cpp` |
| `LLM_MODEL_PATH` | GGUF path for `llama_cpp` | unset |
| `LLM_MODEL` | Model name for Ollama or mock runs | `llama3` for Ollama, `mock` for mock |
| `OLLAMA_BASE_URL` | Ollama API base URL | `http://localhost:11434` |

### Generation and context

| Variable | Purpose | Default |
| :--- | :--- | :--- |
| `LLM_UNIFIED_MODE` | Use unified JSON generation instead of narrator+extractor judge flow | `true` |
| `LLM_N_CTX` | Context window for `llama_cpp` | `4096` |
| `LLM_MAX_TOKENS` | Max generation length | `512` |
| `LLM_TEMPERATURE` | Sampling temperature | `0.6` |
| `LLM_TOKEN_BUDGET` | Story-context budget used by `StoryContext` | half of context window |
| `LLM_SUMMARY_THRESHOLD` | Fraction of token budget that triggers summarization | `0.8` |
| `LLM_SUMMARY_MAX_TOKENS` | Max tokens for summary generations | `200` |
| `LLM_REPAIR_ATTEMPTS` | JSON repair retries | `2` |

### Persistence and telemetry

| Variable | Purpose |
| :--- | :--- |
| `NEO4J_URI` / `NEO4J_USER` / `NEO4J_PASSWORD` | Neo4j connection |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP HTTP exporter endpoint |
| `GRAFANA_PASSWORD` | Grafana admin password in `docker-compose` |

## Development

Install dev tools:

```bash
uv sync --group dev
```

Recommended local checks:

```bash
bash scripts/run_smoke.sh
uv run pytest --cov=cyoa --cov-report=term-missing --cov-report=xml --cov-report=json -q
uv run python scripts/check_coverage.py
uv run ruff check .
uv run mypy cyoa
```

`scripts/run_smoke.sh` currently runs `uv run pytest -q -m smoke`.

Latest verified local CI coverage in this workspace on `2026-04-14`:

- Total: `93.38%`
- `cyoa/core`: `97.30%` against an `83.00%` floor
- `cyoa/llm`: `95.12%` against a `74.00%` floor
- `cyoa/db`: `93.54%` against a `68.00%` floor

GitHub may show a different coverage figure, such as `83.24%`, if it is reading a different report, a different metric, or an older artifact.

## Operational Notes

- Neo4j is optional at runtime. If connectivity or auth fails, the app continues without graph persistence.
- Chroma is optional at runtime. If it cannot initialize, the app falls back to a recent-history memory buffer.
- Save files are written under `saves/`.
- UI preferences are stored in `.config.json`.
- The story transcript logger writes to `story.md`.
