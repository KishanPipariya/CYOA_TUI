from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from cyoa.core.models import Objective, StoryNode


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
    story_context: StoryContextMemento = field(default_factory=StoryContextMemento)

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
            story_context=StoryContextMemento(history=self.story_context.to_payload()),
        )

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
            "story_context_history": self.story_context.to_payload(),
        }

