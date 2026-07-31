# CYOA TUI

AI-generated choose-your-own-adventure fiction in the terminal, built with `Textual`.

![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Textual](https://img.shields.io/badge/UI-Textual-2f6fed)
![llama.cpp](https://img.shields.io/badge/Local%20LLM-llama.cpp-6b8e23)
![Coverage](https://raw.githubusercontent.com/KishanPipariya/CYOA_TUI/main/.github/badges/coverage.svg)
![License](https://img.shields.io/badge/license-MIT-green)

## Install and play

The quickest way to get started is:

```bash
uv sync
uv run cyoa-tui
```

Source installs require Python 3.13 or newer.

If you want local GGUF support from source, install the optional extra first:

```bash
uv sync --extra local-llm
uv run cyoa-tui --runtime-preset local-fast
```

Startup profiles:

```bash
uv run cyoa-tui --runtime-preset mock-smoke
uv run cyoa-tui --runtime-preset local-fast
uv run cyoa-tui --runtime-preset local-quality
```

`mock-smoke` is the safe startup path for a fresh machine: it starts without a local
model and is useful for demos, smoke checks, and verifying the UI.

You can also force key accessibility modes before the full UI renders:

```bash
uv run cyoa-tui --screen-reader --reduced-motion
uv run cyoa-tui --high-contrast
```

These flags apply immediately for the current launch. They do not rewrite saved settings unless you later save the same values from the in-app Settings screen.

On first launch, choose one of these options:

- `Quick Demo` for instant play with the built-in mock backend
- `Download Local Model` to save a recommended GGUF into the app data folder

If you already have a `.gguf` model, launch with `--model /path/to/model.gguf` or
set it from Settings. Local model mode requires the `local-llm` extra from source.

Manual saves, autosaves, exports, settings, models, logs, and crash diagnostics are
stored in standard app-data folders for your platform. If a previous session has an
autosave, startup offers to resume it or start fresh.

If you already have a packaged release build, unpack it and run `./cyoa-tui` from a terminal. The v0.1.0 packaged builds target macOS and Linux; Windows users should run from source for this release.

### Persistence compatibility

Settings use config `version: 1`; saves, autosaves, restore points, and run archives use
`schema_version: 1`. These are strict schemas. Older versionless files, unknown fields,
or malformed values are rejected without being edited, renamed, deleted, or overwritten.
Correct the file from a backup (or remove it yourself if you no longer need it) before
retrying; invalid config files stop startup with their path, and invalid saves or archives
show an in-app error.

## What you get

- streaming narrative turns in a terminal-first UI
- branching choices with keyboard-first navigation
- save/load, undo/redo, restart, bookmarks, exports, a journal, and a story map
- the ability to branch from past scenes without losing your current save history
- recap, character sheet, lore codex, command palette, and notification review flows
- safe startup on fresh machines without requiring a local model immediately
- runtime presets, startup accessibility flags, and customizable keybindings
- config, saves, models, and logs stored in standard user app-data directories

Press `h` for the in-app help overlay, `ctrl+shift+p` for the command palette, and `o` for Settings. From Settings, you can choose a provider, point to a local model, switch theme packs, edit keybindings, and tune verbosity, typewriter behavior, and diagnostics.

## Learn more

- [Consumer Guide](docs/consumer-guide.md)
- [Advanced Setup](docs/advanced-setup.md)
- [Non-Docker Neo4j Setup](docs/non-docker-neo4j.md)

## Demo snapshot

![CYOA TUI live screenshot](docs/assets/Screenshot%202026-04-18%20at%205.49.19%E2%80%AFPM.png)

## Development

Contributor and infrastructure details live in [Advanced Setup](docs/advanced-setup.md).

## Technical references

- [CODEWIKI.md](CODEWIKI.md)
- [workflow.md](workflow.md)
- [loading_art.md](cyoa/resources/loading_art.md)

## License

[MIT](LICENSE)
