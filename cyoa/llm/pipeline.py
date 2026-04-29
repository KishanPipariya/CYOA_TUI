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

    def process(
        self, context: Any, initial_messages: list[dict[str, str]] | None = None
    ) -> list[dict[str, str]]:
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
                companions=getattr(context, "companions", []),
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

    def _inject_into_system(
        self, messages: list[dict[str, str]], text: str
    ) -> list[dict[str, str]]:
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
            "3. You MUST provide a creative 'title' for this new adventure in the JSON response on the first turn.\n"
            "4. Describe changes to the player's inventory and stats (health, gold, reputation) directly in the narrative prose.\n"
            "5. Track discoverable lore with 'lore_entries_updated' for named NPCs, locations, factions, and important items.\n"
            "6. Use 'companions_updated' when recruitable allies join, become active, are lost, or change affinity/effect.\n"
            "7. Set 'mood' to an atmospheric keyword (e.g. 'mysterious', 'heroic', 'combat', 'ethereal', 'dark', 'grimy').\n"
            "8. When the story reaches a definitive conclusion (victory, death, escape, etc), set 'is_ending' to true.\n"
            "9. Ensure your output is strictly valid JSON matching the requested schema."
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
        objectives = getattr(context, "objectives", [])
        if objectives:
            objective_bits = [f"{objective.text} ({objective.status})" for objective in objectives]
            lines.append(f"Objectives: {'; '.join(objective_bits)}")
        faction_reputation = getattr(context, "faction_reputation", {})
        if faction_reputation:
            import json

            lines.append(f"Faction Reputation: {json.dumps(faction_reputation)}")
        npc_affinity = getattr(context, "npc_affinity", {})
        if npc_affinity:
            import json

            lines.append(f"NPC Affinity: {json.dumps(npc_affinity)}")
        companion_lines = self._compact_companion_lines(getattr(context, "companions", []))
        if companion_lines:
            lines.append("Companions:")
            lines.extend(companion_lines)
        story_flags = sorted(getattr(context, "story_flags", set()))
        if story_flags:
            lines.append(f"Unlocked Story Flags: {', '.join(story_flags)}")
        lore_entries = getattr(context, "lore_entries", [])
        compact_lore_lines = self._compact_lore_lines(lore_entries)
        if compact_lore_lines:
            lines.append("Discovered Lore:")
            lines.extend(compact_lore_lines)
        lines.append("</player_sheet>")

        return self._inject_into_system(messages, "\n".join(lines))

    @staticmethod
    def _compact_lore_lines(lore_entries: list[object], *, max_entries: int = 12) -> list[str]:
        grouped: dict[str, list[str]] = {"npc": [], "location": [], "faction": [], "item": []}
        normalized_entries = sorted(
            lore_entries,
            key=lambda entry: (
                getattr(entry, "discovered_turn", None) is None,
                getattr(entry, "discovered_turn", 0),
                getattr(entry, "category", ""),
                getattr(entry, "name", ""),
            ),
        )
        for entry in normalized_entries[:max_entries]:
            category = getattr(entry, "category", "")
            name = str(getattr(entry, "name", "")).strip()
            summary = str(getattr(entry, "summary", "")).strip()
            if category not in grouped or not name or not summary:
                continue
            grouped[category].append(f"- {category.title()}: {name} - {summary}")

        lines: list[str] = []
        for category in ("npc", "location", "faction", "item"):
            lines.extend(grouped[category])
        return lines

    @staticmethod
    def _compact_companion_lines(companions: list[object], *, max_entries: int = 8) -> list[str]:
        normalized_companions = sorted(
            companions,
            key=lambda companion: (
                {"active": 0, "available": 1, "lost": 2}.get(
                    getattr(companion, "status", "available"), 3
                ),
                getattr(companion, "name", "").casefold(),
            ),
        )
        lines: list[str] = []
        for companion in normalized_companions[:max_entries]:
            name = str(getattr(companion, "name", "")).strip()
            status = str(getattr(companion, "status", "available")).strip()
            if not name:
                continue
            affinity = getattr(companion, "affinity", 0)
            if not isinstance(affinity, int):
                affinity = 0
            effect = str(getattr(companion, "effect", "") or "").strip()
            line = f"- {name} ({status}, affinity {affinity})"
            if effect:
                line = f"{line} - Effect: {effect}"
            lines.append(line)
        return lines


class MemoryComponent(PromptComponent, PromptComponentMixin):
    """Injects RAG memories into the system prompt."""

    def transform(self, context: Any, messages: list[dict[str, str]]) -> list[dict[str, str]]:
        memory_entries = getattr(context, "memory_entries", [])
        if memory_entries:
            order = {"scene": 0, "chapter": 1, "entity": 2, "generic": 3}
            lines = ["<memory_retrieval>"]
            sorted_entries = sorted(
                memory_entries,
                key=lambda entry: (
                    order.get(getattr(entry, "category", "generic"), 99),
                    getattr(entry, "source", "") or "",
                    getattr(entry, "text", ""),
                ),
            )
            for i, entry in enumerate(sorted_entries):
                category = getattr(entry, "category", "generic").title()
                source = getattr(entry, "source", None)
                reason = getattr(entry, "reason", "")
                header = f"[{category} Memory]"
                if source:
                    header = f"[{category} Memory: {source}]"
                lines.append(header)
                lines.append(getattr(entry, "text", ""))
                if reason:
                    lines.append(f"Reason: {reason}")
                if i < len(sorted_entries) - 1:
                    lines.append("---")
            lines.append("</memory_retrieval>")
            return self._inject_into_system(messages, "\n".join(lines))

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
