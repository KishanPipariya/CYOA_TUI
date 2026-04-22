# Consumer Guide

This guide is for people who want to install `cyoa-tui`, choose a runtime, and start playing without digging through developer setup details.

## What You Need

- A modern terminal:
  - macOS: Terminal or iTerm2
  - Linux: GNOME Terminal, Konsole, Kitty, Alacritty, or similar
- Enough space for saves and optional local models
- Optional: a local GGUF model if you want offline model-backed play immediately

## Install Paths

### macOS packaged build

If you downloaded a packaged release archive:

1. Unpack it.
2. Open Terminal.
3. `cd` into the unpacked folder.
4. Run `./cyoa-tui`

### Linux packaged build

If you downloaded a packaged release archive:

1. Unpack it.
2. Open a terminal in the unpacked folder.
3. Run `chmod +x cyoa-tui` if needed.
4. Run `./cyoa-tui`

### Run from source

If you are running directly from the repository:

```bash
uv sync
uv run cyoa-tui
```

For local GGUF support from source:

```bash
uv sync --extra local-llm
uv run cyoa-tui
```

## First Launch

On a fresh machine, the app opens with a first-run setup screen.

- `Quick Demo`: starts immediately with the built-in mock backend
- `Download Local Model`: downloads a recommended GGUF into the app data folder for future launches

If your terminal is too small or your machine does not have enough disk or RAM for the recommended model, the app explains the issue instead of failing at startup.

## Settings

Press `o` in the app to open Settings.

Available settings include:

- runtime provider
- local model path
- theme pack
- dark or light mode
- typewriter on or off
- typewriter speed
- diagnostics toggle
- backend test
- reveal save folder
- reset settings to safe defaults

Dark mode and typewriter changes apply immediately. Provider, model path, theme pack, and diagnostics apply after restart.

## Controls

- `1-4`: choose an option
- `up` / `down`: move between choices
- `enter`: confirm the focused choice
- `space`: skip typewriter animation
- `t`: toggle typewriter
- `v`: cycle typewriter speed
- `o`: open settings
- `s` / `l`: save or load
- `u` / `y`: undo or redo
- `j` / `m`: toggle journal or story map
- `e`: export story
- `q`: quit

## Where Your Data Lives

The app stores your files in standard per-user locations instead of the repository directory.

### macOS

- config and saves: `~/Library/Application Support/cyoa-tui/`
- logs: `~/Library/Logs/cyoa-tui/`

### Linux

- config: `~/.config/cyoa-tui/`
- saves and exports: `~/.local/share/cyoa-tui/`
- logs: `~/.local/state/cyoa-tui/`

### Windows

- config: `%APPDATA%\\cyoa-tui\\`
- saves and exports: `%LOCALAPPDATA%\\cyoa-tui\\`
- logs: `%LOCALAPPDATA%\\cyoa-tui\\Logs\\`

## Troubleshooting

### The app starts in demo mode

That usually means no usable local model is configured. Open Settings with `o`, switch to `Local Model`, and set a valid `.gguf` path, or use the first-run download flow.

### The terminal layout looks cramped

Resize the terminal. The UI reads best at `100x28` or larger.

### The model download option is blocked

The preflight checks found a real constraint, usually low RAM or low disk space. Use `Quick Demo` for now or free space before trying again.

### The app crashed during startup

The app writes a crash log with support breadcrumbs to your normal logs folder:

- macOS: `~/Library/Logs/cyoa-tui/last_crash.log`
- Linux: `~/.local/state/cyoa-tui/last_crash.log`
- Windows: `%LOCALAPPDATA%\\cyoa-tui\\Logs\\last_crash.log`

Open Settings and use `Reveal Saves` if you need to inspect your files manually, or `Reset Settings` to go back to safe demo defaults.

### I want the advanced stack

See [Advanced Setup](./advanced-setup.md) for source builds, optional extras, Docker services, monitoring, and development workflow.

### I want Neo4j without Docker

See [Non-Docker Neo4j Setup](./non-docker-neo4j.md).
