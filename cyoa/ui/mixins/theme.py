import logging

from textual.containers import Container
from textual.theme import BUILTIN_THEMES, Theme
from textual.widget import Widget

from cyoa.core import theme_loader, utils
from cyoa.ui.components import ThemeSpinner
from cyoa.ui.mixins.contracts import as_mixin_host, as_textual_app

logger = logging.getLogger(__name__)


class ThemeMixin:
    """Mixin for theme and mood management."""

    def apply_ui_theme(self) -> None:
        """Apply optional theme-specific surface styling to mounted widgets."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        ui_theme = getattr(host, "_ui_theme", None)
        if not isinstance(ui_theme, dict) or not ui_theme:
            return

        direct_surfaces = {
            "#main-container": ui_theme.get("main_surface"),
            "#action-panel": ui_theme.get("action_dock_surface"),
            "#status-display": ui_theme.get("status_surface"),
        }
        for selector, color in direct_surfaces.items():
            if not isinstance(color, str) or not color.strip():
                continue
            try:
                app.query_one(selector, Widget).set_styles(f"background: {color};")
            except Exception as e:
                logger.debug("Failed to apply ui theme style to %s: %s", selector, e)

        side_panel_surface = ui_theme.get("side_panel_surface")
        if isinstance(side_panel_surface, str) and side_panel_surface.strip():
            for widget in app.query(".side-panel-shell"):
                widget.set_styles(f"background: {side_panel_surface};")

        self._apply_ui_theme_to_dynamic_content()

    def _apply_ui_theme_to_dynamic_content(self) -> None:
        """Apply theme-specific styling to story and choice widgets created during play."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        ui_theme = getattr(host, "_ui_theme", None)
        if not isinstance(ui_theme, dict) or not ui_theme:
            return

        widget_styles = {
            ".story-turn.current-turn": ui_theme.get("story_card_surface"),
            ".story-turn.archived-turn": ui_theme.get("story_card_muted_surface"),
            ".player-choice": ui_theme.get("player_choice_surface"),
            "#choices-container .choice-card-available": ui_theme.get("choice_surface"),
            "#choices-container .choice-card-locked": ui_theme.get("choice_locked_surface"),
        }
        for selector, color in widget_styles.items():
            if not isinstance(color, str) or not color.strip():
                continue
            for widget in app.query(selector):
                widget.set_styles(f"background: {color};")

    def watch_mood(self, old_mood: str, new_mood: str) -> None:
        """Update the main container class and application theme when the mood changes."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        try:
            container = app.query_one("#main-container", Container)
            container.remove_class(f"mood-{old_mood}")
            container.add_class(f"mood-{new_mood}")

            # Look up atmospheric theme in themes.json
            mood_config = theme_loader.get_config_for_mood(new_mood)

            if mood_config:
                # 1. Update Spinner frames
                try:
                    spinner = app.query_one("#loading", ThemeSpinner)
                    if "spinner_frames" in mood_config:
                        spinner.frames = mood_config["spinner_frames"]
                        spinner._frame_idx = 0
                        spinner.update(spinner.frames[0])
                except Exception as e:
                    logger.debug("Failed to update spinner frames for mood %s: %s", new_mood, e)

                # 2. Update App Theme (accent color)
                accent = mood_config.get("accent_color")
                if accent:
                    base_theme_name = "textual-dark" if host.dark else "textual-light"
                    base_theme = BUILTIN_THEMES.get(base_theme_name)
                    if base_theme:
                        theme_name = f"mood-{new_mood}"
                        # Re-register theme with new accent
                        app.register_theme(
                            Theme(
                                name=theme_name,
                                primary=base_theme.primary,
                                secondary=base_theme.secondary,
                                warning=base_theme.warning,
                                error=base_theme.error,
                                success=base_theme.success,
                                accent=accent,
                                foreground=base_theme.foreground,
                                background=base_theme.background,
                                surface=base_theme.surface,
                                panel=base_theme.panel,
                                boost=base_theme.boost,
                                dark=base_theme.dark,
                            )
                        )
                        app.theme = theme_name
                self._apply_ui_theme_to_dynamic_content()
        except Exception as e:
            logger.debug("Mood watch update failed from %s to %s: %s", old_mood, new_mood, e)

    def action_toggle_dark(self) -> None:
        """Toggle dark mode and persist preference."""
        host = as_mixin_host(self)
        host.dark = not host.dark
        config = utils.load_config()
        config["dark"] = host.dark
        utils.save_config(config)

    def _apply_custom_accent(self, accent_color: str) -> None:
        """Apply a custom accent color to the current theme."""
        app = as_textual_app(self)
        host = as_mixin_host(self)
        base_theme_name = "textual-dark" if host.dark else "textual-light"
        base_theme = BUILTIN_THEMES.get(base_theme_name)
        if base_theme:
            app.register_theme(
                Theme(
                    name="cyoa-custom",
                    primary=base_theme.primary,
                    secondary=base_theme.secondary,
                    warning=base_theme.warning,
                    error=base_theme.error,
                    success=base_theme.success,
                    accent=accent_color,
                    foreground=base_theme.foreground,
                    background=base_theme.background,
                    surface=base_theme.surface,
                    panel=base_theme.panel,
                    boost=base_theme.boost,
                    dark=base_theme.dark,
                )
            )
            app.theme = "cyoa-custom"
