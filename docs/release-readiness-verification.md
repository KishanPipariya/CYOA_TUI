# Release Readiness Verification

Updated: 2026-04-29

This pass closes the Phase 0 manual UI verification slice with reproducible terminal checks from the local Textual test harness.

## Verified Layout Sizes

- Standard-width terminal: `160x42`
- Compact terminal: `100x34`
- Narrow rescue-mode terminal: `72x24`
- Small modal-boundary terminal: `60x20`

## Verified Theme Coverage

- `dark_dungeon`
- `space_explorer`
- `haunted_observatory`

These theme checks rely on both runtime layout assertions and theme accessibility validation for shipped UI surfaces.

## Commands Run

```bash
uv run pytest tests/test_tui.py -k 'layout or narrow_terminal or large_text or story_entries_and_player_choice_borders or accessibility_matrix' tests/test_themes.py
uv run pytest tests/test_ui_units.py -k 'inventory or help_text_covers_branching_exports_and_review_panels or modal_screens_dismiss_expected_values' tests/test_tui.py -k 'inventory_inspector or scene_recap_screen_opens_during_live_play or shipped_themes_render_stable_layouts or modal_dialog_borders_do_not_clip_on_small_terminals'
```

## Outcome

- Standard and compact layouts stayed within screen bounds.
- Narrow rescue mode kept drawer panels and action controls reachable.
- Large-text accessibility presets kept help/settings and core play widgets inside the viewport.
- Shipped themes remained structurally stable across standard and compact widths.
- Theme validation continued to enforce contrast separation for muted and locked surfaces.
