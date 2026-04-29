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

## Quick Start Recipes

Use one of these depending on how you want to play:

```bash
uv run cyoa-tui --runtime-preset mock-smoke
uv run cyoa-tui --runtime-preset local-fast
uv run cyoa-tui --runtime-preset local-quality
```

- `mock-smoke` starts the lightweight demo runtime.
- `local-fast` prefers the local llama.cpp provider with a balanced preset.
- `local-quality` prefers the local llama.cpp provider with a more deliberate preset.

If no working local model is configured, the app falls back safely instead of failing during normal startup.

## Startup Accessibility Flags

If you need an accessible startup mode before the full UI appears, launch with one or more of these flags:

```bash
uv run cyoa-tui --screen-reader
uv run cyoa-tui --high-contrast
uv run cyoa-tui --reduced-motion
```

You can combine them. These flags apply immediately for the current session and do not change your saved config unless you later save the same values from Settings.

## Launch Options You May Actually Use

- `--runtime-preset ...` applies a saved startup profile such as `mock-smoke`, `local-fast`, or `local-quality`.
- `--theme ...` starts with a specific story theme from the installed theme pack.
- `--prompt ...` bypasses the theme prompt and starts from your custom opening prompt.
- `--preset ...` selects the generation preset directly at launch.
- `--model /path/to/model.gguf` points the local runtime at a specific GGUF file for this launch.

## First Launch

On a fresh machine, the app opens with a first-run setup screen.

- `Quick Demo`: starts immediately with the built-in mock backend
- `Download Local Model`: downloads a recommended GGUF into the app data folder for future launches

If your terminal is too small or your machine does not have enough disk or RAM for the recommended model, the app explains the issue instead of failing at startup.

## Settings

Press `o` in the app to open Settings.

Available settings include:

- runtime provider
- runtime preset and generation preset
- local model path
- theme pack
- customizable keybindings
- dark or light mode
- typewriter on or off
- typewriter speed
- notification, recap, runtime, and locked-choice verbosity
- diagnostics toggle
- backend test
- reveal save folder
- reset settings to safe defaults

Dark mode and typewriter changes apply immediately. Provider, runtime profile, model path, theme pack, keybindings, and diagnostics are reflected by the app once the new settings are applied or the app is restarted, depending on the control.

## Controls

- `1-4`, `up` / `down`, and `enter` handle your main choice selection loop.
- `space` skips the current typewriter animation, while `t` and `v` toggle or speed-cycle narrated text.
- `h` opens help and `ctrl+shift+p` opens the command palette for searchable action discovery.
- `o` opens Settings, where you can change provider, theme, keybindings, verbosity, and accessibility options.
- `j` and `m` toggle the journal and story map side panels.
- `i` opens the scene recap, `c` opens the character sheet, and `z` opens the lore codex.
- `[` opens the journal summary and `]` opens the story-map summary in a text-first review format.
- `n` repeats the latest status update and `shift+n` opens notification history.
- `s` saves, `l` loads, and `e` exports the current run as markdown, accessible markdown, and JSON.
- `u` and `y` undo or redo the latest turn change.
- `b` branches from a past scene, `k` creates a bookmark, and `p` restores a bookmark.
- `g` cycles the active generation preset and `x` edits comma-separated directives for the current run.
- `r` restarts the adventure and `q` quits with confirmation.

All keybindings can be changed in Settings. The footer hints, help sheet, and command palette follow your saved bindings.

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

That usually means no usable local model is configured. Open Settings with `o`, switch to the local model configuration, and set a valid `.gguf` path, use the first-run download flow, or start explicitly with `--runtime-preset mock-smoke`.

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
