import logging
from typing import Any, ClassVar

from cyoa.core.events import Events, bus
from cyoa.core.models import Objective, StoryNode

logger = logging.getLogger(__name__)


class GameState:
    """Manages the current progress, inventory, stats, and nodes for the StoryEngine.

    Separates state mutations and snapshot management from the orchestration core.
    """

    # Single source of truth for default player stats. Add a new stat here and
    # it propagates to __init__, reset(), and any code that copies _DEFAULT_STATS.
    _DEFAULT_STATS: ClassVar[dict[str, int]] = {"health": 100, "gold": 0, "reputation": 0}

    def __init__(
        self,
        inventory: list[str] | None = None,
        player_stats: dict[str, int] | None = None,
    ) -> None:
        self.inventory: list[str] = inventory or []
        self.player_stats: dict[str, int] = dict(player_stats) if player_stats else dict(self._DEFAULT_STATS)
        self.turn_count: int = 1
        self.current_node: StoryNode | None = None
        self.story_title: str | None = None
        self.current_scene_id: str | None = None
        self.last_choice_text: str | None = None
        self.timeline_metadata: list[dict[str, Any]] = []
        self.objectives: list[Objective] = []
        self.faction_reputation: dict[str, int] = {}
        self.npc_affinity: dict[str, int] = {}
        self.story_flags: set[str] = set()

        # Snapshot for one-level undo
        self._undo_snapshot: dict[str, Any] | None = None

    def reset(self) -> None:
        """Reset the game state to its initial state."""
        self.inventory = []
        self.player_stats = dict(self._DEFAULT_STATS)
        self.turn_count = 1
        self.current_node = None
        self.story_title = None
        self.current_scene_id = None
        self.last_choice_text = None
        self.timeline_metadata = []
        self.objectives = []
        self.faction_reputation = {}
        self.npc_affinity = {}
        self.story_flags = set()
        self._undo_snapshot = None

    def apply_node_updates(self, node: StoryNode) -> None:
        """Update local state from node feedback (stats, inventory)."""
        # 1. Update Stats
        stats_changed = False
        for stat, change in node.stat_updates.items():
            if change != 0:
                self.player_stats[stat] = self.player_stats.get(stat, 0) + change
                stats_changed = True

        if stats_changed:
            bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))

        # 2. Update Inventory
        inv_changed = False
        for item in node.items_gained:
            if item not in self.inventory:
                self.inventory.append(item)
                inv_changed = True
        for item in node.items_lost:
            if item in self.inventory:
                self.inventory.remove(item)
                inv_changed = True

        if inv_changed:
            bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))

        world_state_changed = self._apply_world_updates(node)
        if world_state_changed:
            bus.emit(Events.WORLD_STATE_UPDATED, state=self.get_world_state())

        # 3. Advance state
        self.current_node = node

    def create_undo_snapshot(self, extra_data: dict[str, Any] | None = None) -> None:
        """Capture the current state to allow for a future 'undo' operation."""
        snapshot = {
            "turn_count": self.turn_count,
            "current_node": self.current_node,
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
        }
        if extra_data:
            snapshot.update(extra_data)
        self._undo_snapshot = snapshot

    def undo(self) -> bool:
        """Revert the state to the previous snapshot."""
        if not self._undo_snapshot:
            return False

        snap = self._undo_snapshot
        self.turn_count = snap["turn_count"]
        self.current_node = snap["current_node"]
        self.inventory = list(snap["inventory"])
        self.player_stats = dict(snap["player_stats"])
        self.story_title = snap["story_title"]
        self.current_scene_id = snap["current_scene_id"]
        self.last_choice_text = snap["last_choice_text"]
        self.timeline_metadata = [entry.copy() for entry in snap.get("timeline_metadata", [])]
        self.objectives = self._coerce_objectives(snap.get("objectives"))
        self.faction_reputation = self._coerce_relationships(snap.get("faction_reputation"))
        self.npc_affinity = self._coerce_relationships(snap.get("npc_affinity"))
        self.story_flags = self._coerce_story_flags(snap.get("story_flags"))

        # Snapshot used; clear it
        self._undo_snapshot = None

        # Emit refresh events
        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        bus.emit(Events.WORLD_STATE_UPDATED, state=self.get_world_state())

        # Refresh narrative node
        if self.current_node:
            bus.emit(Events.NODE_COMPLETED, node=self.current_node)

        return True

    def get_save_data(self) -> dict[str, Any]:
        """Convert current state into a serializable dictionary."""
        return {
            "story_title": self.story_title,
            "turn_count": self.turn_count,
            "inventory": self.inventory,
            "player_stats": self.player_stats,
            "current_node": self.current_node.model_dump() if self.current_node else None,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
            "timeline_metadata": [entry.copy() for entry in self.timeline_metadata],
            "objectives": [objective.model_dump() for objective in self.objectives],
            "faction_reputation": dict(self.faction_reputation),
            "npc_affinity": dict(self.npc_affinity),
            "story_flags": sorted(self.story_flags),
        }

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate state from dictionary data."""
        self.story_title = data.get("story_title") if isinstance(data.get("story_title"), str) else None
        self.turn_count = self._coerce_positive_int(data.get("turn_count"), default=1)
        self.inventory = self._coerce_inventory(data.get("inventory"))
        self.player_stats = self._coerce_player_stats(data.get("player_stats"))
        self.current_scene_id = (
            data.get("current_scene_id") if isinstance(data.get("current_scene_id"), str) else None
        )
        self.last_choice_text = (
            data.get("last_choice_text") if isinstance(data.get("last_choice_text"), str) else None
        )
        self.timeline_metadata = self._coerce_timeline_metadata(data.get("timeline_metadata"))
        self.objectives = self._coerce_objectives(data.get("objectives"))
        self.faction_reputation = self._coerce_relationships(data.get("faction_reputation"))
        self.npc_affinity = self._coerce_relationships(data.get("npc_affinity"))
        self.story_flags = self._coerce_story_flags(data.get("story_flags"))

        node_data = data.get("current_node")
        if not isinstance(node_data, dict):
            self.current_node = None
        else:
            try:
                self.current_node = StoryNode(**node_data)
            except Exception:
                logger.warning("Ignoring malformed current_node in save payload.")
                self.current_node = None

        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        bus.emit(Events.WORLD_STATE_UPDATED, state=self.get_world_state())
        if self.current_node:
            bus.emit(Events.NODE_COMPLETED, node=self.current_node)

        bus.emit(Events.STORY_TITLE_GENERATED, title=self.story_title)

    def _coerce_positive_int(self, value: Any, *, default: int) -> int:
        """Return a positive integer fallback when save data is malformed."""
        if isinstance(value, bool):
            return default
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return parsed if parsed > 0 else default

    def _coerce_inventory(self, value: Any) -> list[str]:
        """Normalize inventory data to a simple list of strings."""
        if not isinstance(value, list):
            return []
        return [item for item in value if isinstance(item, str)]

    def _coerce_player_stats(self, value: Any) -> dict[str, int]:
        """Merge saved stat values onto the default stat set."""
        stats = dict(self._DEFAULT_STATS)
        if not isinstance(value, dict):
            return stats
        for key, raw in value.items():
            if not isinstance(key, str) or isinstance(raw, bool):
                continue
            try:
                stats[key] = int(raw)
            except (TypeError, ValueError):
                continue
        return stats

    def _coerce_timeline_metadata(self, value: Any) -> list[dict[str, Any]]:
        """Normalize saved timeline metadata into a predictable structure."""
        if not isinstance(value, list):
            return []

        normalized: list[dict[str, Any]] = []
        for entry in value:
            normalized_entry = self._normalize_timeline_entry(entry)
            if normalized_entry is not None:
                normalized.append(normalized_entry)

        return normalized

    def _normalize_timeline_entry(self, entry: Any) -> dict[str, Any] | None:
        """Normalize a single timeline metadata entry."""
        if not isinstance(entry, dict):
            return None

        kind = entry.get("kind")
        if not isinstance(kind, str):
            return None

        normalized_entry: dict[str, Any] = {"kind": kind}
        for key in ("source_scene_id", "target_scene_id"):
            value = entry.get(key)
            if isinstance(value, str):
                normalized_entry[key] = value

        restored_turn = self._coerce_optional_int(entry.get("restored_turn"))
        if restored_turn is not None:
            normalized_entry["restored_turn"] = restored_turn

        return normalized_entry

    def _coerce_optional_int(self, value: Any) -> int | None:
        """Parse optional integer values from save data."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return None
        return None

    def get_world_state(self) -> dict[str, Any]:
        """Return tracked long-lived world state for prompt injection and UI display."""
        return {
            "objectives": [objective.model_dump() for objective in self.objectives],
            "faction_reputation": dict(self.faction_reputation),
            "npc_affinity": dict(self.npc_affinity),
            "story_flags": sorted(self.story_flags),
        }

    def seed_world_state(
        self,
        *,
        inventory: list[str] | None = None,
        player_stats: dict[str, int] | None = None,
        objectives: list[Objective] | None = None,
        faction_reputation: dict[str, int] | None = None,
        npc_affinity: dict[str, int] | None = None,
        story_flags: set[str] | None = None,
    ) -> None:
        """Apply initial state from a theme or imported save payload."""
        if inventory is not None:
            self.inventory = list(dict.fromkeys(inventory))
        if player_stats is not None:
            self.player_stats = self._coerce_player_stats(player_stats)
        if objectives is not None:
            self.objectives = [objective.model_copy() for objective in objectives]
        if faction_reputation is not None:
            self.faction_reputation = dict(faction_reputation)
        if npc_affinity is not None:
            self.npc_affinity = dict(npc_affinity)
        if story_flags is not None:
            self.story_flags = set(story_flags)

    def _apply_world_updates(self, node: StoryNode) -> bool:
        changed = False

        objective_index = {objective.id: idx for idx, objective in enumerate(self.objectives)}
        for objective in node.objectives_updated:
            existing_idx = objective_index.get(objective.id)
            if existing_idx is None:
                self.objectives.append(objective.model_copy())
                objective_index[objective.id] = len(self.objectives) - 1
                changed = True
                continue
            existing = self.objectives[existing_idx]
            if existing.text != objective.text or existing.status != objective.status:
                self.objectives[existing_idx] = objective.model_copy()
                changed = True

        for faction, delta in node.faction_updates.items():
            if delta == 0:
                continue
            self.faction_reputation[faction] = self.faction_reputation.get(faction, 0) + delta
            changed = True

        for npc, delta in node.npc_affinity_updates.items():
            if delta == 0:
                continue
            self.npc_affinity[npc] = self.npc_affinity.get(npc, 0) + delta
            changed = True

        for flag in node.story_flags_set:
            if flag not in self.story_flags:
                self.story_flags.add(flag)
                changed = True
        for flag in node.story_flags_cleared:
            if flag in self.story_flags:
                self.story_flags.remove(flag)
                changed = True

        return changed

    def _coerce_objectives(self, value: Any) -> list[Objective]:
        if not isinstance(value, list):
            return []
        objectives: list[Objective] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                objectives.append(Objective(**item))
            except Exception:
                continue
        return objectives

    def _coerce_relationships(self, value: Any) -> dict[str, int]:
        relationships: dict[str, int] = {}
        if not isinstance(value, dict):
            return relationships
        for key, raw in value.items():
            if not isinstance(key, str) or isinstance(raw, bool):
                continue
            try:
                relationships[key] = int(raw)
            except (TypeError, ValueError):
                continue
        return relationships

    def _coerce_story_flags(self, value: Any) -> set[str]:
        if not isinstance(value, list):
            return set()
        return {flag for flag in value if isinstance(flag, str) and flag}
