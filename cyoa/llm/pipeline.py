from abc import ABC, abstractmethod
from typing import Any


class PromptComponent(ABC):
    """Abstract base class for a component that contributes to the LLM prompt."""

    @abstractmethod
    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        """
        Transforms the message stack based on the story context.
        context is expected to be a StoryContext instance (avoiding circular import).
        """
        pass


class PromptPipeline:
    """Coordinates a sequence of PromptComponents to build the final message stack."""

    def __init__(self, components: list[PromptComponent] | None = None) -> None:
        self.components = components or []

    def add_component(self, component: PromptComponent) -> None:
        self.components.append(component)

    def process(self, context: Any, initial_messages: list[dict[str, str]] | None = None) -> list[dict[str, str]]:
        """Executes all components in order."""
        messages = initial_messages or []
        for component in self.components:
            messages = component.transform(context, messages)
        return messages


class SystemMessageComponent(PromptComponent):
    """
    A foundational component that manages the primary 'system' message.
    It can wrap a Jinja2 template or just a static string.
    """

    def __init__(self, template_name: str | None = None, static_content: str | None = None) -> None:
        self.template_name = template_name
        self.static_content = static_content

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        content = ""
        if self.static_content:
            content = self.static_content
        elif self.template_name and hasattr(context, "jinja_env"):
            template = context.jinja_env.get_template(self.template_name)
            # We pass the entire context attributes as keywords
            content = template.render(
                inventory=getattr(context, "inventory", []),
                stats=getattr(context, "player_stats", {}),
                memories=getattr(context, "memories", []),
                scene_summary=getattr(context, "scene_summary", None),
                chapter_summary=getattr(context, "chapter_summary", None),
                arc_summary=getattr(context, "arc_summary", None),
            )

        # Check if there's already a system message to append to or replace
        for msg in messages:
            if msg["role"] == "system":
                msg["content"] = (msg["content"] + "\n\n" + content).strip()
                return messages

        # Otherwise insert at the beginning
        messages.insert(0, {"role": "system", "content": content.strip()})
        return messages


class PromptComponentMixin:
    """Helper to inject text into the first system message in space-efficient way."""

    def _inject_into_system(self, messages: list[dict[str, str]], text: str) -> list[dict[str, str]]:
        if not text:
            return messages

        # Search for an existing system message
        for msg in messages:
            if msg["role"] == "system":
                # Ensure we have double-newlines for readability between blocks
                current_content = msg.get("content", "").strip()
                if current_content:
                    msg["content"] = f"{current_content}\n\n{text.strip()}"
                else:
                    msg["content"] = text.strip()
                return messages

        # Fallback: create a system message at the start
        messages.insert(0, {"role": "system", "content": text.strip()})
        return messages


class PersonaComponent(PromptComponent, PromptComponentMixin):
    """Injects core narrative personality and constraints."""

    def __init__(self, persona_text: str | None = None) -> None:
        # Default instructions if none provided
        self.persona_text = persona_text or (
            "You are a dark fantasy interactive fiction engine.\n"
            "1. Maintain a gritty atmospheric tone. Stay in character as the Narrator.\n"
            "2. Provide 2-3 choices for what they can do next.\n"
            "3. You MUST provide a creative 'title' for this new adventure in the JSON response.\n"
            "4. Manage the player's inventory using 'items_gained' and 'items_lost'. Track when they acquire or lose items.\n"
            "5. Manage the player's stats (health, gold, reputation) using 'stat_updates'. Provide stat changes "
            "(e.g. {'health': -10, 'gold': 50}) when the narrative dictates it.\n"
            "6. Set 'mood' to an atmospheric keyword (e.g. 'mysterious', 'heroic', 'combat', 'ethereal', 'dark', 'grimy').\n"
            "7. When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true "
            "and provide an empty choices list.\n"
            "8. Ensure your output is strictly valid JSON matching the requested schema."
        )

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        return self._inject_into_system(messages, f"## Narrative Persona\n{self.persona_text}")


class PlayerSheetComponent(PromptComponent, PromptComponentMixin):
    """Injects current inventory and stats into the system prompt."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        inventory = getattr(context, "inventory", [])
        stats = getattr(context, "player_stats", {})

        lines = ["<player_sheet>"]
        if inventory:
            lines.append(f"Current Inventory: {', '.join(inventory)}")
        else:
            lines.append("Current Inventory: Empty")

        if stats:
            import json

            lines.append(f"Current Stats: {json.dumps(stats)}")
        lines.append("</player_sheet>")

        return self._inject_into_system(messages, "\n".join(lines))


class MemoryComponent(PromptComponent, PromptComponentMixin):
    """Injects RAG memories into the system prompt."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        memories = getattr(context, "memories", [])
        if not memories:
            return messages

        lines = ["<memory_retrieval>", "[Memory — relevant past scenes for context]"]
        for i, mem in enumerate(memories):
            lines.append(mem)
            if i < len(memories) - 1:
                lines.append("---")
        lines.append("</memory_retrieval>")

        return self._inject_into_system(messages, "\n".join(lines))


class SummarizationComponent(PromptComponent, PromptComponentMixin):
    """Injects hierarchical summaries into the system prompt."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        scene_summary = getattr(context, "scene_summary", None)
        chapter_summary = getattr(context, "chapter_summary", None)
        arc_summary = getattr(context, "arc_summary", None)

        blocks = []
        if arc_summary:
            blocks.append(f"<arc_summary>\n[Global Plot Synopsis]\n{arc_summary}\n</arc_summary>")
        if chapter_summary:
            blocks.append(
                f"<chapter_summary>\n[Previous Chapter Context]\n{chapter_summary}\n</chapter_summary>"
            )
        if scene_summary:
            blocks.append(f"<scene_summary>\n[Last Scene Recap]\n{scene_summary}\n</scene_summary>")

        if blocks:
            return self._inject_into_system(messages, "\n\n".join(blocks))

        return messages


class GoalComponent(PromptComponent, PromptComponentMixin):
    """Injects current narrative goals or directives into the system prompt."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        goals = getattr(context, "goals", [])
        if not goals:
            return messages

        lines = ["## Current Narrative Goals"]
        for goal in goals:
            lines.append(f"- {goal}")

        return self._inject_into_system(messages, "\n".join(lines))


class DirectiveComponent(PromptComponent, PromptComponentMixin):
    """Injects transient player-provided 'directives' or 'toggles'."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        directives = getattr(context, "directives", [])
        if not directives:
            return messages

        lines = ["## Active Directives"]
        for directive in directives:
            lines.append(f"! {directive}")

        return self._inject_into_system(messages, "\n".join(lines))


class HistoryComponent(PromptComponent):
    """Injects the list of interaction history from the context."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        history = getattr(context, "history", [])
        # We append history at the end of whatever we already have
        return messages + history
