# Advanced Setup

This document keeps optional infrastructure and contributor tooling out of the main end-user flow.

## Source Install

Base install:

```bash
uv sync
```

Optional extras:

```bash
uv sync --extra local-llm
uv sync --extra graph --extra memory --extra observability
uv sync --extra packaging
```

Full contributor setup:

```bash
uv sync --extra all --group dev
```

Enable the repo-managed Git hooks for your clone:

```bash
bash scripts/install_git_hooks.sh
```

Hook behavior:

- `pre-commit`: Ruff lint and format checks for staged Python files, plus theme validation when theme files change.
- `pre-push`: smoke suite, repo-wide Ruff, and `mypy cyoa`.

## Runtime Overrides

Environment variables are available as advanced overrides:

```env
LLM_PROVIDER=llama_cpp
LLM_MODEL_PATH=/path/to/model.gguf
LLM_PRESET=balanced
APP_RUNTIME_PRESET=local-fast
```

Supported providers:

- `mock`
- `llama_cpp`

## Optional Services

Bring up the local infrastructure stack only if you need graph persistence and telemetry:

```bash
docker-compose up -d
```

Included services:

- Neo4j
- Jaeger v2
- OpenTelemetry Collector
- Prometheus
- Grafana

Chroma is used in-process and is not launched by `docker-compose`.

If you want Neo4j graph persistence without Docker, see [Non-Docker Neo4j Setup](./non-docker-neo4j.md).

## Packaged Builds

Build standalone terminal bundles for macOS or Linux:

```bash
uv sync --extra packaging
uv run python scripts/build_binary.py
```

Artifacts are emitted under `dist/pyinstaller/cyoa-tui/`.

## Validation And Quality Gates

```bash
uv run python scripts/validate_themes.py
bash scripts/run_smoke.sh
uv run pytest -q
uv run pytest --cov=cyoa --cov-report=term-missing --cov-report=json -q
uv run python scripts/check_coverage.py
uv run ruff check .
uv run mypy cyoa
```

## Technical References

- [CodeWiki](../CODEWIKI.md)
- [Architecture Flow](../workflow.md)
- [Loading Art](../loading_art.md)
