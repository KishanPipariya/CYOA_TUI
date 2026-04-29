from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from cyoa.core.models import LoreEntry, Objective, StoryNode

if TYPE_CHECKING:
    from cyoa.core.state import GameState


@dataclass(slots=True)
class StoryContextMemento:
    """Serializable subset of story-context state needed for restore flows."""

    history: list[dict[str, str]] = field(default_factory=list)

    def to_payload(self) -> list[dict[str, str]]:
        return [message.copy() for message in self.history]

    @classmethod
    def from_payload(cls, payload: Any) -> StoryContextMemento:
        if not isinstance(payload, list):
            return cls()

        history: list[dict[str, str]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            role = item.get("role")
            content = item.get("content")
            if isinstance(role, str) and isinstance(content, str):
                history.append({"role": role, "content": content})
        return cls(history=history)


@dataclass(slots=True)
class GameStateSnapshot:
    """Typed memento for GameState undo/redo/bookmark persistence."""

    turn_count: int
    current_node: StoryNode | None
    inventory: list[str]
    player_stats: dict[str, int]
    story_title: str | None
    current_scene_id: str | None
    last_choice_text: str | None
    timeline_metadata: list[dict[str, Any]]
    objectives: list[Objective]
    faction_reputation: dict[str, int]
    npc_affinity: dict[str, int]
    story_flags: set[str]
    lore_entries: list[LoreEntry]
    story_context: StoryContextMemento = field(default_factory=StoryContextMemento)

    @classmethod
    def from_game_state(
        cls,
        state: GameState,
        *,
        story_context_history: Any = None,
    ) -> GameStateSnapshot:
        """Build a typed snapshot from a live game state."""
        return cls(
            turn_count=state.turn_count,
            current_node=state.current_node.model_copy()
            if state.current_node is not None
            else None,
            inventory=list(state.inventory),
            player_stats=dict(state.player_stats),
            story_title=state.story_title,
            current_scene_id=state.current_scene_id,
            last_choice_text=state.last_choice_text,
            timeline_metadata=[entry.copy() for entry in state.timeline_metadata],
            objectives=[objective.model_copy() for objective in state.objectives],
            faction_reputation=dict(state.faction_reputation),
            npc_affinity=dict(state.npc_affinity),
            story_flags=set(state.story_flags),
            lore_entries=[entry.model_copy() for entry in state.lore_entries],
            story_context=StoryContextMemento.from_payload(story_context_history),
        )

    def clone(self) -> GameStateSnapshot:
        return GameStateSnapshot(
            turn_count=self.turn_count,
            current_node=self.current_node.model_copy() if self.current_node is not None else None,
            inventory=list(self.inventory),
            player_stats=dict(self.player_stats),
            story_title=self.story_title,
            current_scene_id=self.current_scene_id,
            last_choice_text=self.last_choice_text,
            timeline_metadata=[entry.copy() for entry in self.timeline_metadata],
            objectives=[objective.model_copy() for objective in self.objectives],
            faction_reputation=dict(self.faction_reputation),
            npc_affinity=dict(self.npc_affinity),
            story_flags=set(self.story_flags),
            lore_entries=[entry.model_copy() for entry in self.lore_entries],
            story_context=StoryContextMemento(history=self.story_context.to_payload()),
        )

    def restore_game_state(self, state: GameState) -> None:
        """Apply this snapshot to a live game state."""
        state.turn_count = self.turn_count
        state.current_node = (
            self.current_node.model_copy() if self.current_node is not None else None
        )
        state.inventory = list(self.inventory)
        state.player_stats = dict(self.player_stats)
        state.story_title = self.story_title
        state.current_scene_id = self.current_scene_id
        state.last_choice_text = self.last_choice_text
        state.timeline_metadata = [entry.copy() for entry in self.timeline_metadata]
        state.objectives = [objective.model_copy() for objective in self.objectives]
        state.faction_reputation = dict(self.faction_reputation)
        state.npc_affinity = dict(self.npc_affinity)
        state.story_flags = set(self.story_flags)
        state.lore_entries = [entry.model_copy() for entry in self.lore_entries]

    def to_payload(self) -> dict[str, Any]:
        return {
            "turn_count": self.turn_count,
            "current_node": self.current_node.model_dump() if self.current_node else None,
            "inventory": list(self.inventory),
            "player_stats": dict(self.player_stats),
            "story_title": self.story_title,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
            "timeline_metadata": [entry.copy() for entry in self.timeline_metadata],
            "objectives": [objective.model_dump() for objective in self.objectives],
            "faction_reputation": dict(self.faction_reputation),
            "npc_affinity": dict(self.npc_affinity),
            "story_flags": sorted(self.story_flags),
            "lore_entries": [entry.model_dump() for entry in self.lore_entries],
            "story_context_history": self.story_context.to_payload(),
        }
