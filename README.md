# CYOA TUI

AI-generated choose-your-own-adventure fiction in the terminal, built with `Textual`.

![Python](https://img.shields.io/badge/python-3.13%2B-blue)
![Textual](https://img.shields.io/badge/UI-Textual-2f6fed)
![llama.cpp](https://img.shields.io/badge/Local%20LLM-llama.cpp-6b8e23)
![Coverage](https://raw.githubusercontent.com/KishanPipariya/CYOA_TUI/main/.github/badges/coverage.svg)
![License](https://img.shields.io/badge/license-MIT-green)

## Install And Play

The fastest path is:

```bash
uv sync
uv run cyoa-tui
```

On first launch, choose one of these:

- `Quick Demo` for instant play with the built-in mock backend
- `Download Local Model` to save a recommended GGUF into the app data folder

If you already have a packaged release build, unpack it and run `./cyoa-tui` from a terminal.

## What The App Does

- streams narrative turns in a terminal-first UI
- presents branching choices with keyboard-first navigation
- supports saves, load, undo/redo, bookmarks, exports, journal, and story map
- runs safely on fresh machines without forcing a local model at startup
- stores config, saves, models, and logs in standard user app-data directories

Press `o` in the app to open Settings for provider choice, local model path, theme pack, typewriter speed, and diagnostics.

## Consumer Docs

- [Consumer Guide](docs/consumer-guide.md)
- [Advanced Setup](docs/advanced-setup.md)

## Demo Snapshot

![CYOA TUI live screenshot](docs/assets/Screenshot%202026-04-18%20at%205.49.19%E2%80%AFPM.png)

## Development

Contributor and infrastructure details live in [Advanced Setup](docs/advanced-setup.md).

## Technical References

- [CODEWIKI.md](CODEWIKI.md)
- [workflow.md](workflow.md)
- [loading_art.md](loading_art.md)

## License

[MIT](LICENSE)
