# Accessibility Compatibility Matrix

This matrix defines the repeatable checks used before release for the highest-risk terminal and accessibility combinations.

## Automated Coverage

| Cell | Coverage | Why It Matters |
| --- | --- | --- |
| Screen Reader Friendly + Reduced Motion + Cognitive Load Reduction + no typewriter | `tests/test_tui.py::test_accessibility_matrix_export_supports_combined_mode` | Confirms the most constrained combined mode can complete a turn and produce a clean accessible transcript. |
| Screen Reader Friendly startup recommendation | `tests/test_tui.py::test_startup_accessibility_recommendation_*` | Confirms terminal fallback advice is surfaced before play begins. |
| Accessibility diagnostics export | `tests/test_tui.py::test_accessibility_diagnostics_snapshot_exports_redacted_runtime_state` | Confirms support snapshots redact runtime details while preserving active accessibility state. |
| Small terminal modal layout | `tests/test_tui.py::test_modal_dialog_borders_do_not_clip_on_small_terminals` | Catches clipped dialogs in compact layouts. |
| Theme contrast validation | `tests/test_themes.py` and `scripts/validate_themes.py` | Blocks shipped themes with unreadable foreground/surface combinations. |

## Manual Cells

| Cell | Required Check |
| --- | --- |
| Standard width, default profile | Start a run, make two choices, open help, settings, inventory, codex, replay, and export. |
| Compact width, default profile | Repeat the same flow and verify choices, dialogs, and side panels remain reachable without clipped text. |
| Screen Reader Friendly profile | Confirm plain labels, hidden ASCII art, stable focus return, and accessible summaries for journal/map. |
| High Contrast profile | Confirm status, choices, disabled choices, modal borders, and notifications preserve contrast. |
| Reduced Motion profile | Confirm typewriter/spinner behavior is reduced and replay/settings dialogs do not rely on animation. |
| Keyboard-only profile | Complete save, load, replay, branch, and export flows without pointer input. |

## Remaining Manual Risk

- Real screen reader output quality still needs manual verification on the user's terminal and assistive technology.
- Terminal color capabilities vary enough that fallback mode should be spot-checked on at least one low-color terminal.
- PDF export remains intentionally out of scope until user demand justifies adding a document-rendering dependency and a separate accessibility review path.
