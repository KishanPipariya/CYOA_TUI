# AI Choose-Your-Own-Adventure (CYOA) TUI
A dark fantasy Choose-Your-Own-Adventure game generated entirely by a local Large Language Model (LLM) and played through a Terminal User Interface (TUI). Every choice branches the narrative in real-time.

## Features
* **Endless Real-Time Generation**: Story scenarios and player choices are entirely dynamically created by a local LLM (defaults to Qwen 2.5 14B Q5) holding context via `llama.cpp`. 
* **Textual TUI interface**: A fully responsive terminal interface with markdown rendering, choice buttons, scrollable history, and a rich ASCII-art loading screen. 
* **State Graph Persistence [Optional]**: Integrates an optional local Neo4j Docker container. The game automatically saves every scene, the choices presented at that scene, and the branching edges to a Graph Database—automatically partitioning parallel playthroughs by the LLM's generated "Story Title".

## Architecture 
* `app.py`: The Textual `App` container that controls layout, handles user input, streams LLM output, and maps state variables.
* `llm_backend.py`: The `llama-cpp-python` wrapper configured to enforce strict JSON structured grammars, maintaining an internal Context array to keep the LLM on track.
* `models.py`: Defining the Pydantic schema the LLM is forced to return (A string narrative, an optional Title, and a list of 2-4 string Choices).
* `graph_db.py`: The Neo4j graph driver wrapper. Gracefully fails if a connection cannot be established, allowing the TUI to continue running losslessly. 

## Requirements
* Python 3.13+
* **UV**: Project dependencies are managed by `uv`.
* **LLM weights**: You must provide a valid local `.gguf` file matching the model argument during execution.
* *[Optional]* **Docker**: To run the backend Neo4j graph container. 

## How To Run

1. Clone the repository and install dependencies:
```bash
uv sync   # or uv add textual llama-cpp-python pydantic neo4j
```

2. *(Optional)* Start the graph database:
```bash
docker-compose up -d
```
*(Neo4j visual web browser will be active on `localhost:7474`, default credentials: User `neo4j` | Password `cyoa_password`)*

3. Run the console application, pointing to your local LLM weights:
```bash
uv run python main.py --model qwen2.5-14b-instruct-q5_k_m.gguf
```

## Logs
A flat, readable chronologic backlog of the session is appended to `story.md` as you play.