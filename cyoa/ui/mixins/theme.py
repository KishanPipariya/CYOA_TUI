import logging

from textual.app import App
from textual.theme import BUILTIN_THEMES, Theme

from cyoa.core import theme_loader, utils
from cyoa.ui.components import ThemeSpinner

logger = logging.getLogger(__name__)

class ThemeMixin:
    """Mixin for theme and mood management."""

    def watch_mood(self, old_mood: str, new_mood: str) -> None:
        """Update the main container class and application theme when the mood changes."""
        assert isinstance(self, App)
        try:
            container = self.query_one("#main-container")
            container.remove_class(f"mood-{old_mood}")
            container.add_class(f"mood-{new_mood}")

            # Look up atmospheric theme in themes.json
            mood_config = theme_loader.get_config_for_mood(new_mood)

            if mood_config:
                # 1. Update Spinner frames
                try:
                    spinner = self.query_one("#loading", ThemeSpinner)
                    if "spinner_frames" in mood_config:
                        spinner.frames = mood_config["spinner_frames"]
                        spinner._frame_idx = 0
                except Exception as e:
                    logger.debug("Failed to update spinner frames for mood %s: %s", new_mood, e)

                # 2. Update App Theme (accent color)
                accent = mood_config.get("accent_color")
                if accent:
                    base_theme_name = "textual-dark" if self.dark else "textual-light"
                    base_theme = BUILTIN_THEMES.get(base_theme_name)
                    if base_theme:
                        theme_name = f"mood-{new_mood}"
                        # Re-register theme with new accent
                        self.register_theme(
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
                        self.theme = theme_name
        except Exception as e:
            logger.debug("Mood watch update failed from %s to %s: %s", old_mood, new_mood, e)

    def action_toggle_dark(self) -> None:
        """Toggle dark mode and persist preference."""
        assert isinstance(self, App)
        self.dark = not self.dark
        config = utils.load_config()
        config["dark"] = self.dark
        utils.save_config(config)

    def _apply_custom_accent(self, accent_color: str) -> None:
        """Apply a custom accent color to the current theme."""
        assert isinstance(self, App)
        base_theme_name = "textual-dark" if self.dark else "textual-light"
        base_theme = BUILTIN_THEMES.get(base_theme_name)
        if base_theme:
            self.register_theme(
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
            self.theme = "cyoa-custom"
