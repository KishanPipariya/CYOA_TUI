# Inventory Combination Evaluation

Updated: 2026-04-29

This note closes the roadmap item to evaluate item-combination resolution via LLM judgment.

## Current Fit

- The current engine supports one player choice at a time plus structured extraction of state changes.
- Inventory already persists cleanly through save/load, undo/redo, bookmarks, and branching.
- Lore discovery for items now gives the UI enough context for inspect-first review without changing engine semantics.

## Risks Of Adding Free-Form Combination Judgment Now

- A combine flow needs a new intent model because current choices do not encode multi-item selection.
- LLM-only judgment would make success/failure less predictable than the existing structured choice path.
- Ambiguous extraction becomes more likely when a combine action both consumes items and reveals new state.

## Recommendation

- Keep combination judgment deferred for now.
- If revisited later, gate it behind an explicit `Combine Items` action with:
  - deterministic item selection in the UI
  - a structured extraction schema for consumed items, created items, and unlocked flags
  - prompt context limited to the selected items plus current scene stakes
