# CYOA TUI: AI-Driven Narrative Engine

A dark fantasy Choose-Your-Adventure game generated entirely by a local Large Language Model (LLM) through a sophisticated Terminal User Interface (TUI). Every choice branches the narrative in real-time, tracked in a persistent graph database.

![Python 3.13+](https://img.shields.io/badge/python-3.13+-blue.svg)
![Textual](https://img.shields.io/badge/UI-Textual-orange.svg)
![LLM Inference](https://img.shields.io/badge/LLM-LlamaCpp-green.svg)
![Observability](https://img.shields.io/badge/Observability-OpenTelemetry-purple.svg)

## Key Features

*   **Endless Real-Time Generation**: Story scenarios and choices are dynamically created by a local LLM (defaults to Qwen 2.5 14B Q5) via `llama.cpp`.
*   **Immersive TUI**: Built with **Textual**, featuring:
    *   **Typewriter Narrator**: Character-by-character reveals for atmospheric storytelling.
    *   **Smart Scrolling**: Auto-scrolling that respects manual reading position.
    *   **Animated Transitions**: Smooth panel animations and loading sequences.
    *   **Markdown Rendering**: Rich text for descriptions and dialogue.
*   **Resilient LLM Pipeline**:
    *   **Repair Loop**: Automatically detects and fixes malformed JSON from the LLM.
    *   **Model Brokers**: Supports fallbacks and alternative providers.
    *   **Token Budgeting**: Smart context management using `tiktoken` to prevent overflow and maintain long-term coherence.
*   **Persistent Graph World**: Every playthrough is saved to a **Neo4j** graph database, mapping choices to narrative branches for a truly persistent multiverse.
*   **RAG Memory**: Long-term story consistency powered by **ChromaDB** for vector-based memory retrieval.
*   **Deep Observability**: Full **OpenTelemetry** integration. Trace every prompt, latency, and token usage via Jaeger, and monitor system health through Prometheus & Grafana.

---

## Architecture

The codebase follows a modular, event-driven architecture designed for scalability and maintainability:

*   **`cyoa.core`**: The backbone. Custom **Event Bus**, Pydantic models, theme loaders, and global constants.
*   **`cyoa.ui`**: The Textual frontend. Modular components, ASCII art engine, and `app.py` coordinator.
*   **`cyoa.llm`**: Intelligent brokerage. Handles prompt templating, provider logic (LlamaCpp), and the resilient JSON repair loop.
*   **`cyoa.db`**: Storage layer. Neo4j graph driver, ChromaDB RAG memory, and disk-based story logging.
*   **`monitoring`**: Configuration for the observability stack (OTLP Collector, Prometheus, Grafana).

---

## Quick Start

### Prerequisites
*   Python 3.13+
*   **[uv](https://github.com/astral-sh/uv)**: Blazing fast Python package management.
*   **Docker**: Required for the observability and database stack.
*   **LLM Weights**: A local `.gguf` file (e.g., Qwen 2.5 14B).

### 1. Installation
Clone the repository and sync dependencies:
```bash
uv sync
```

### 2. Configure Environment Variables
Copy the example environment file and adjust as needed:
```bash
cp .env.example .env
```
Key variables to configure:
*   **`LLM_MODEL_PATH`**: Path to your local `.gguf` file (e.g., `models/qwen2.5-14b.gguf`).
*   **`NEO4J_PASSWORD`**: Your Neo4j database password (setup in `.env`).
*   **`OTEL_EXPORTER_OTLP_ENDPOINT`**: OTLP endpoint for observability (default: `http://localhost:4318`).

### 3. Launch Infrastructure
Start the database and observability containers:
```bash
docker-compose up -d
```
*   **Neo4j UI**: `http://localhost:7474` (User: `neo4j` / Pass: `your_configured_password`)
*   **Jaeger Traces**: `http://localhost:16686`
*   **Grafana Dashboards**: `http://localhost:3001` (Admin / admin)

### 4. Start the Adventure
Run the application (it will use the model path from `.env` or you can override it):
```bash
uv run python main.py --model path/to/model.gguf
```

---

## CLI Usage

| Argument | Description |
| :--- | :--- |
| `--model` | **Required**. Path to the local `.gguf` file. |
| `--theme` | Theme choice (e.g., `dark_dungeon`, `space_explorer`). |
| `--prompt` | Override starting prompt directly. |

---

## Configuration Reference

The following environment variables can be set in your `.env` file:

| Variable | Description | Default |
| :--- | :--- | :--- |
| `LLM_MODEL_PATH` | Path to your local GGUF model file. | `models/...` |
| `LLM_PROVIDER` | LLM backend: `llama_cpp`, `ollama`, or `mock`. | `llama_cpp` |
| `NEO4J_URI` | Neo4j connection URI. | `bolt://localhost:7687` |
| `NEO4J_USER` | Neo4j username. | `neo4j` |
| `NEO4J_PASSWORD` | Neo4j password. | *(Required)* |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | OTLP collector endpoint. | *(None)* |
| `LLM_N_CTX` | Model context window size. | `4096` |
| `LLM_TEMPERATURE` | Narrative sampling temperature. | `0.6` |
| `LLM_MAX_TOKENS` | Max tokens for narrative generation. | `512` |

---

## Development and Quality

*   **Testing**: Comprehensive `pytest` suite with async support (`uv run pytest`).
*   **Typing**: Strict `mypy` enforcement (`uv run mypy .`).
*   **Linting/Formatting**: Blazing fast `ruff` integration (`uv run ruff check .`).
