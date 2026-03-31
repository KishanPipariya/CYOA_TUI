import logging
from typing import Any

from cyoa.core.events import Events, bus
from cyoa.core.models import StoryNode

logger = logging.getLogger(__name__)


class GameState:
    """Manages the current progress, inventory, stats, and nodes for the StoryEngine.

    Separates state mutations and snapshot management from the orchestration core.
    """

    def __init__(
        self,
        inventory: list[str] | None = None,
        player_stats: dict[str, int] | None = None,
    ) -> None:
        self.inventory: list[str] = inventory or []
        self.player_stats: dict[str, int] = player_stats or {"health": 100, "gold": 0, "reputation": 0}
        self.turn_count: int = 1
        self.current_node: StoryNode | None = None
        self.story_title: str | None = None
        self.current_scene_id: str | None = None
        self.last_choice_text: str | None = None

        # Snapshot for one-level undo
        self._undo_snapshot: dict[str, Any] | None = None

    def reset(self) -> None:
        """Reset the game state to its initial state."""
        self.inventory = []
        self.player_stats = {"health": 100, "gold": 0, "reputation": 0}
        self.turn_count = 1
        self.current_node = None
        self.story_title = None
        self.current_scene_id = None
        self.last_choice_text = None
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

        # Snapshot used; clear it
        self._undo_snapshot = None

        # Emit refresh events
        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))

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
        }

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate state from dictionary data."""
        self.story_title = data.get("story_title")
        self.turn_count = data.get("turn_count", 1)
        self.inventory = data.get("inventory", [])
        self.player_stats = data.get("player_stats", {"health": 100, "gold": 0, "reputation": 0})
        self.current_scene_id = data.get("current_scene_id")
        self.last_choice_text = data.get("last_choice_text")

        node_data = data.get("current_node")
        if node_data:
            self.current_node = StoryNode(**node_data)
        else:
            self.current_node = None

        bus.emit(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        if self.current_node:
            bus.emit(Events.NODE_COMPLETED, node=self.current_node)

        bus.emit(Events.STORY_TITLE_GENERATED, title=self.story_title)
