# Changelog

## Unreleased

- **Breaking:** persistence is now strict and versioned. Config files require `version: 1`;
  saves, autosaves, restore points, and run archives require `schema_version: 1`.
  Legacy/versionless or malformed files are preserved and rejected with recovery guidance
  instead of being silently coerced, migrated, or overwritten.
- **Breaking:** story persistence now uses the built-in local SQLite database at
  `stories.sqlite3`; legacy remote configuration and migration support were removed.

## 0.1.0 - 2026-05-11

Initial public release candidate.

- Terminal-first choose-your-own-adventure UI built with Textual.
- Safe first-run setup with instant demo mode and optional local GGUF download.
- Local llama.cpp runtime presets for faster or higher-quality play.
- Theme packs, save/load, undo/redo, branching, bookmarks, exports, journal, story map, recap, character sheet, and lore codex.
- Accessibility startup flags, configurable keybindings, reduced motion, high contrast, and screen-reader-friendly review flows.
- Local story persistence, optional retrieval memory, and observability for advanced users.
