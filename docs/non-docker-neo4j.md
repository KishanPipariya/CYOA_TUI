# Non-Docker Neo4j Setup

This guide is for running `cyoa-tui` with Neo4j-backed graph persistence without `docker-compose`.

Graph persistence is optional. If Neo4j is unavailable, the app continues without it.

## When You Need This

Use this setup only if you want one of these:

- persistent graph storage for stories and scenes
- direct access to the Neo4j browser or Cypher queries
- a native or remote Neo4j install instead of Docker

For normal play, you do not need Neo4j at all.

## Repo Requirements

Install the optional graph dependency from the repo root:

```bash
uv sync --extra graph
```

The app loads `.env` automatically at startup, so repo-local environment variables are enough.

## Neo4j Server

Run Neo4j either:

- natively on your machine
- as a managed or remote Neo4j instance you can reach over Bolt

This repo expects Bolt at `bolt://localhost:7687` by default.

## Recommended App User

Create a dedicated Neo4j user for the app instead of using an admin account.

Suggested properties:

- separate credentials for `cyoa-tui`
- access limited to the target database
- only the privileges needed to read, create, and update story data

The app does not require full Neo4j administration privileges for day-to-day use.

## Configure The Repo

Create a `.env` file in the repo root. You can start from `.env.example`.

```env
CYOA_ENABLE_GRAPH_DB=true
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=cyoa_app
NEO4J_PASSWORD=your-password
```

If you are using a remote Neo4j instance, change `NEO4J_URI` to that host.

## One-Time Schema Setup

Run these statements once against the target database:

```cypher
CREATE CONSTRAINT story_id_unique IF NOT EXISTS FOR (s:Story) REQUIRE s.id IS UNIQUE;
CREATE CONSTRAINT story_title_unique IF NOT EXISTS FOR (s:Story) REQUIRE s.title IS UNIQUE;
CREATE CONSTRAINT scene_id_unique IF NOT EXISTS FOR (s:Scene) REQUIRE s.id IS UNIQUE;
CREATE INDEX scene_story_title IF NOT EXISTS FOR (s:Scene) ON (s.story_title);
```

These match the schema hardening statements exposed by the app.

## Start The App

From the repo root:

```bash
uv run cyoa-tui
```

Or:

```bash
uv run python main.py
```

If the Neo4j credentials are valid and the server is reachable, the app will use graph persistence.

## Troubleshooting

### The app starts but graph persistence is missing

Check these first:

- `uv sync --extra graph` was run successfully
- `.env` exists in the repo root
- `CYOA_ENABLE_GRAPH_DB=true` is set, or Neo4j env vars are present
- `NEO4J_URI`, `NEO4J_USER`, and `NEO4J_PASSWORD` are correct
- the Neo4j server is listening on the Bolt endpoint you configured

### The app still works when Neo4j is down

That is expected. This repo treats graph persistence as optional and falls back cleanly when Neo4j is offline.

### Should a normal user get direct DB access?

Usually no. Most users only need the app itself.

Give direct Neo4j access only to users who need to inspect or manage graph data, and prefer a dedicated least-privilege user over an admin account.
