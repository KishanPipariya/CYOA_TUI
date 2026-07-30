import logging
from collections import deque
from dataclasses import dataclass
from typing import Any, ClassVar

from cyoa.core.events import Events, bus
from cyoa.core.mementos import GameStateSnapshot, StoryContextMemento
from cyoa.core.models import (
    CampaignChapter,
    CampaignClock,
    CampaignPack,
    CampaignProgress,
    Companion,
    LoreEntry,
    Objective,
    StoryNode,
    WorldTime,
)

logger = logging.getLogger(__name__)

MAX_HISTORY_DEPTH = 20


@dataclass(frozen=True, slots=True)
class _LoadedSaveScalars:
    story_title: str | None
    turn_count: int
    current_scene_id: str | None
    last_choice_text: str | None
    last_choice_submission: str | None


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
        self.inventory: list[str] = list(inventory) if inventory is not None else []
        self.player_stats: dict[str, int] = (
            dict(player_stats) if player_stats else dict(self._DEFAULT_STATS)
        )
        self.turn_count: int = 1
        self.current_node: StoryNode | None = None
        self.story_title: str | None = None
        self.current_scene_id: str | None = None
        self.last_choice_text: str | None = None
        self.last_choice_submission: str | None = None
        self.timeline_metadata: list[dict[str, Any]] = []
        self.objectives: list[Objective] = []
        self.faction_reputation: dict[str, int] = {}
        self.npc_affinity: dict[str, int] = {}
        self.story_flags: set[str] = set()
        self.lore_entries: list[LoreEntry] = []
        self.companions: list[Companion] = []
        self.world_time: WorldTime = WorldTime()
        self.campaign: CampaignPack | None = None
        self.campaign_progress: CampaignProgress | None = None
        self._undo_history: deque[GameStateSnapshot] = deque(maxlen=MAX_HISTORY_DEPTH)
        self._redo_history: deque[GameStateSnapshot] = deque(maxlen=MAX_HISTORY_DEPTH)
        self._bookmarks: dict[str, GameStateSnapshot] = {}
        self._last_restored_snapshot: GameStateSnapshot | None = None

    def reset(self) -> None:
        """Reset the game state to its initial state."""
        self.inventory = []
        self.player_stats = dict(self._DEFAULT_STATS)
        self.turn_count = 1
        self.current_node = None
        self.story_title = None
        self.current_scene_id = None
        self.last_choice_text = None
        self.last_choice_submission = None
        self.timeline_metadata = []
        self.objectives = []
        self.faction_reputation = {}
        self.npc_affinity = {}
        self.story_flags = set()
        self.lore_entries = []
        self.companions = []
        self.world_time = WorldTime()
        self.campaign = None
        self.campaign_progress = None
        self._undo_history.clear()
        self._redo_history.clear()
        self._bookmarks = {}
        self._last_restored_snapshot = None

    def apply_node_updates(self, node: StoryNode) -> None:
        """Update local state from node feedback (stats, inventory)."""
        # 1. Update Stats
        stats_changed = False
        for stat, change in node.stat_updates.items():
            if change != 0:
                self.player_stats[stat] = self.player_stats.get(stat, 0) + change
                stats_changed = True

        if stats_changed:
            bus.emit_runtime(Events.STATS_UPDATED, stats=dict(self.player_stats))

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
            bus.emit_runtime(Events.INVENTORY_UPDATED, inventory=list(self.inventory))

        world_state_changed = self._apply_world_updates(node)
        time_changed = self._apply_time_advance(node.time_advance_hours)
        clock_changed = self._apply_campaign_clock_updates(node.campaign_clock_updates)
        campaign_changed = self._update_campaign_progress()
        if world_state_changed or time_changed or clock_changed or campaign_changed:
            bus.emit_runtime(Events.WORLD_STATE_UPDATED, state=self.get_world_state())

        # 3. Advance state
        self.current_node = node

    def _build_snapshot(self, extra_data: dict[str, Any] | None = None) -> GameStateSnapshot:
        """Capture the current state in a serializable snapshot."""
        return GameStateSnapshot.from_game_state(
            self,
            story_context_history=(
                extra_data.get("story_context_history") if isinstance(extra_data, dict) else None
            ),
        )

    def create_undo_snapshot(self, extra_data: dict[str, Any] | None = None) -> None:
        """Capture the current state to allow future undo/redo operations."""
        self._undo_history.append(self._build_snapshot(extra_data))
        self._redo_history.clear()

    def _restore_snapshot(self, snap: GameStateSnapshot) -> None:
        """Restore state from a previously captured snapshot."""
        snap.restore_game_state(self)
        self._last_restored_snapshot = snap.clone()

    def _emit_state_refresh_events(self) -> None:
        """Emit events after non-incremental state restoration."""
        bus.emit_runtime(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit_runtime(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        bus.emit_runtime(Events.WORLD_STATE_UPDATED, state=self.get_world_state())

        if self.current_node:
            bus.emit_runtime(Events.NODE_COMPLETED, node=self.current_node)

    def undo(self) -> bool:
        """Revert the state to the previous snapshot."""
        if not self._undo_history:
            return False

        self._redo_history.append(self._build_snapshot())
        self._restore_snapshot(self._undo_history.pop())
        self._emit_state_refresh_events()
        return True

    def redo(self) -> bool:
        """Re-apply the most recently undone snapshot."""
        if not self._redo_history:
            return False

        self._undo_history.append(self._build_snapshot())
        self._restore_snapshot(self._redo_history.pop())
        self._emit_state_refresh_events()
        return True

    def create_bookmark(
        self,
        name: str,
        *,
        extra_data: dict[str, Any] | None = None,
    ) -> bool:
        """Store a named checkpoint for later restoration."""
        normalized = name.strip()
        if not normalized:
            return False
        self._bookmarks[normalized] = self._build_snapshot(extra_data)
        return True

    def restore_bookmark(self, name: str) -> bool:
        """Restore a named checkpoint while preserving undo history."""
        snap = self._bookmarks.get(name)
        if snap is None:
            return False
        self._undo_history.append(self._build_snapshot())
        self._redo_history.clear()
        self._restore_snapshot(snap)
        self._emit_state_refresh_events()
        return True

    def list_bookmarks(self) -> list[str]:
        """Return bookmark names in creation/update order."""
        return list(self._bookmarks)

    def get_save_data(self) -> dict[str, Any]:
        """Convert current state into a serializable dictionary."""
        return {
            "story_title": self.story_title,
            "turn_count": self.turn_count,
            "inventory": list(self.inventory),
            "player_stats": dict(self.player_stats),
            "current_node": self.current_node.model_dump() if self.current_node else None,
            "current_scene_id": self.current_scene_id,
            "last_choice_text": self.last_choice_text,
            "last_choice_submission": self.last_choice_submission,
            "timeline_metadata": [entry.copy() for entry in self.timeline_metadata],
            "objectives": [objective.model_dump() for objective in self.objectives],
            "faction_reputation": dict(self.faction_reputation),
            "npc_affinity": dict(self.npc_affinity),
            "story_flags": sorted(self.story_flags),
            "lore_entries": [entry.model_dump() for entry in self.lore_entries],
            "companions": [companion.model_dump() for companion in self.companions],
            "world_time": self.world_time.model_dump(),
            "campaign": self.campaign.model_dump() if self.campaign else None,
            "campaign_progress": (
                self.campaign_progress.model_dump() if self.campaign_progress else None
            ),
            "campaign_clocks": (
                [clock.model_dump() for clock in self.campaign_progress.clocks]
                if self.campaign_progress
                else []
            ),
            "undo_history": [snapshot.to_payload() for snapshot in self._undo_history],
            "redo_history": [snapshot.to_payload() for snapshot in self._redo_history],
            "bookmarks": {
                name: snapshot.to_payload() for name, snapshot in self._bookmarks.items()
            },
        }

    def load_save_data(self, data: dict[str, Any]) -> None:
        """Hydrate state from dictionary data."""
        scalars = self._read_save_scalars(data)
        self.story_title = scalars.story_title
        self.turn_count = scalars.turn_count
        self.inventory = self._coerce_inventory(data.get("inventory"))
        self.player_stats = self._coerce_player_stats(data.get("player_stats"))
        self.current_scene_id = scalars.current_scene_id
        self.last_choice_text = scalars.last_choice_text
        self.last_choice_submission = scalars.last_choice_submission
        self.timeline_metadata = self._coerce_timeline_metadata(data.get("timeline_metadata"))
        self.objectives = self._coerce_objectives(data.get("objectives"))
        self.faction_reputation = self._coerce_relationships(data.get("faction_reputation"))
        self.npc_affinity = self._coerce_relationships(data.get("npc_affinity"))
        self.story_flags = self._coerce_story_flags(data.get("story_flags"))
        self.lore_entries = self._coerce_lore_entries(data.get("lore_entries"))
        self.companions = self._coerce_companions(data.get("companions"))
        self.world_time = self._coerce_world_time(data.get("world_time"))
        self.campaign = self._coerce_campaign(data.get("campaign"))
        self.campaign_progress = self._coerce_campaign_progress(
            data.get("campaign_progress"),
            campaign=self.campaign,
        )
        if self.campaign_progress is not None and not self.campaign_progress.clocks:
            self.campaign_progress.clocks = self._coerce_campaign_clocks(
                data.get("campaign_clocks")
            )
        self._undo_history = deque(
            self._coerce_snapshot_list(data.get("undo_history")), maxlen=MAX_HISTORY_DEPTH
        )
        self._redo_history = deque(
            self._coerce_snapshot_list(data.get("redo_history")), maxlen=MAX_HISTORY_DEPTH
        )
        self._bookmarks = self._coerce_bookmarks(data.get("bookmarks"))
        self.current_node = self._coerce_current_node(data.get("current_node"))

        self._emit_loaded_state_events()

    def _read_save_scalars(self, data: dict[str, Any]) -> _LoadedSaveScalars:
        last_choice_text = self._coerce_optional_str(data.get("last_choice_text"))
        last_choice_submission = self._coerce_optional_str(data.get("last_choice_submission"))
        return _LoadedSaveScalars(
            story_title=self._coerce_optional_str(data.get("story_title")),
            turn_count=self._coerce_positive_int(data.get("turn_count"), default=1),
            current_scene_id=self._coerce_optional_str(data.get("current_scene_id")),
            last_choice_text=last_choice_text,
            last_choice_submission=last_choice_submission or last_choice_text,
        )

    def _coerce_current_node(self, node_data: Any) -> StoryNode | None:
        if not isinstance(node_data, dict):
            return None
        try:
            return StoryNode(**node_data)
        except Exception:
            logger.warning("Ignoring malformed current_node in save payload.")
            return None

    def _emit_loaded_state_events(self) -> None:
        bus.emit_runtime(Events.STATS_UPDATED, stats=dict(self.player_stats))
        bus.emit_runtime(Events.INVENTORY_UPDATED, inventory=list(self.inventory))
        bus.emit_runtime(Events.WORLD_STATE_UPDATED, state=self.get_world_state())
        if self.current_node:
            bus.emit_runtime(Events.NODE_COMPLETED, node=self.current_node)

        bus.emit_runtime(Events.STORY_TITLE_GENERATED, title=self.story_title)

    @staticmethod
    def _coerce_optional_str(value: Any) -> str | None:
        return value if isinstance(value, str) else None

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
            "lore_entries": [entry.model_dump() for entry in self.lore_entries],
            "companions": [companion.model_dump() for companion in self.companions],
            "world_time": self.world_time.model_dump(),
            "campaign": self.campaign.model_dump() if self.campaign else None,
            "campaign_progress": (
                self.campaign_progress.model_dump() if self.campaign_progress else None
            ),
            "campaign_clocks": (
                [clock.model_dump() for clock in self.campaign_progress.clocks]
                if self.campaign_progress
                else []
            ),
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
        lore_entries: list[LoreEntry] | None = None,
        companions: list[Companion] | None = None,
        world_time: WorldTime | dict[str, int] | None = None,
        campaign: CampaignPack | dict[str, Any] | None = None,
        campaign_progress: CampaignProgress | dict[str, Any] | None = None,
    ) -> None:
        """Apply initial state from a theme or imported save payload."""
        self._seed_core_world_state(
            inventory=inventory,
            player_stats=player_stats,
            objectives=objectives,
            faction_reputation=faction_reputation,
            npc_affinity=npc_affinity,
            story_flags=story_flags,
            lore_entries=lore_entries,
            companions=companions,
            world_time=world_time,
        )
        if campaign is not None:
            self.campaign = self._coerce_campaign(campaign)
        if campaign_progress is not None or self.campaign is not None:
            self.campaign_progress = self._coerce_campaign_progress(
                campaign_progress,
                campaign=self.campaign,
            )
        self._update_campaign_progress()

    def _seed_core_world_state(
        self,
        *,
        inventory: list[str] | None,
        player_stats: dict[str, int] | None,
        objectives: list[Objective] | None,
        faction_reputation: dict[str, int] | None,
        npc_affinity: dict[str, int] | None,
        story_flags: set[str] | None,
        lore_entries: list[LoreEntry] | None,
        companions: list[Companion] | None,
        world_time: WorldTime | dict[str, int] | None,
    ) -> None:
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
        if lore_entries is not None:
            self.lore_entries = [entry.model_copy() for entry in lore_entries]
        if companions is not None:
            self.companions = [companion.model_copy() for companion in companions]
        if world_time is not None:
            self.world_time = self._coerce_world_time(world_time)

    def active_campaign_chapter(self) -> CampaignChapter | None:
        if self.campaign is None or self.campaign_progress is None:
            return None
        return self.campaign.get_chapter(self.campaign_progress.active_chapter_id)

    def active_chapter_directives(self) -> list[str]:
        chapter = self.active_campaign_chapter()
        if chapter is None:
            return []
        return list(chapter.directives)

    def continuity_audit_notes(self) -> list[str]:
        """Return compact local-first continuity reminders for prompt injection."""
        notes: list[str] = []
        for objective in self.objectives:
            notes.append(f"Objective {objective.id}: {objective.status} - {objective.text}")
        if self.last_choice_text:
            notes.append(f"Last player choice is canon: {self.last_choice_text}")
        active_companions = [
            companion.name for companion in self.companions if companion.status == "active"
        ]
        lost_companions = [
            companion.name for companion in self.companions if companion.status == "lost"
        ]
        if active_companions:
            notes.append(f"Active companions: {', '.join(active_companions)}")
        if lost_companions:
            notes.append(
                f"Lost companions must not reappear as active: {', '.join(lost_companions)}"
            )
        if self.story_flags:
            notes.append(f"Established flags: {', '.join(sorted(self.story_flags))}")
        if self.lore_entries:
            recent_lore = sorted(
                self.lore_entries,
                key=lambda entry: entry.discovered_turn or 0,
                reverse=True,
            )[:5]
            for entry in recent_lore:
                notes.append(f"Lore {entry.category}/{entry.name}: {entry.summary}")
        if self.campaign_progress and self.campaign_progress.clocks:
            notes.append(
                "Campaign pressure: "
                + ", ".join(clock.terse_summary() for clock in self.campaign_progress.clocks)
            )
        return notes[:16]

    def _apply_objective_updates(self, objectives_updated: list[Objective]) -> bool:
        changed = False
        objective_index = {objective.id: idx for idx, objective in enumerate(self.objectives)}
        for objective in objectives_updated:
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
        return changed

    @staticmethod
    def _apply_delta_map(target: dict[str, int], updates: dict[str, int]) -> bool:
        changed = False
        for key, delta in updates.items():
            if delta == 0:
                continue
            target[key] = target.get(key, 0) + delta
            changed = True
        return changed

    def _apply_flag_updates(self, flags_set: list[str], flags_cleared: list[str]) -> bool:
        changed = False
        for flag in flags_set:
            if flag not in self.story_flags:
                self.story_flags.add(flag)
                changed = True
        for flag in flags_cleared:
            if flag in self.story_flags:
                self.story_flags.remove(flag)
                changed = True
        return changed

    def _apply_lore_entry_updates(self, lore_entries_updated: list[LoreEntry]) -> bool:
        changed = False
        entry_index = {
            (entry.category, entry.name.casefold()): idx
            for idx, entry in enumerate(self.lore_entries)
        }
        for entry in lore_entries_updated:
            key = (entry.category, entry.name.casefold())
            normalized = entry.model_copy(
                update={
                    "discovered_turn": (
                        entry.discovered_turn
                        if entry.discovered_turn is not None
                        else self.turn_count
                    )
                }
            )
            existing_idx = entry_index.get(key)
            if existing_idx is None:
                self.lore_entries.append(normalized)
                entry_index[key] = len(self.lore_entries) - 1
                changed = True
                continue

            existing = self.lore_entries[existing_idx]
            merged = existing.model_copy(
                update={
                    "summary": normalized.summary,
                    "discovered_turn": (
                        existing.discovered_turn
                        if existing.discovered_turn is not None
                        else normalized.discovered_turn
                    ),
                }
            )
            if merged != existing:
                self.lore_entries[existing_idx] = merged
                changed = True
        return changed

    def _apply_companion_updates(self, companions_updated: list[Companion]) -> bool:
        changed = False
        companion_index = {
            companion.name.casefold(): idx for idx, companion in enumerate(self.companions)
        }
        for companion in companions_updated:
            existing_idx = companion_index.get(companion.name.casefold())
            if existing_idx is None:
                self.companions.append(companion.model_copy())
                companion_index[companion.name.casefold()] = len(self.companions) - 1
                changed = True
                continue
            existing = self.companions[existing_idx]
            merged = existing.model_copy(
                update={
                    "status": companion.status,
                    "affinity": companion.affinity,
                    "summary": companion.summary
                    if companion.summary is not None
                    else existing.summary,
                    "effect": companion.effect if companion.effect is not None else existing.effect,
                }
            )
            if merged != existing:
                self.companions[existing_idx] = merged
                changed = True
        return changed

    def _apply_time_advance(self, hours: int) -> bool:
        if hours <= 0:
            return False
        updated_time = self.world_time.advance(hours)
        if updated_time == self.world_time:
            return False
        self.world_time = updated_time
        return True

    def _apply_world_updates(self, node: StoryNode) -> bool:
        changed = self._apply_objective_updates(node.objectives_updated)
        changed = self._apply_delta_map(self.faction_reputation, node.faction_updates) or changed
        changed = self._apply_delta_map(self.npc_affinity, node.npc_affinity_updates) or changed
        changed = (
            self._apply_flag_updates(node.story_flags_set, node.story_flags_cleared) or changed
        )
        changed = self._apply_lore_entry_updates(node.lore_entries_updated) or changed
        changed = self._apply_companion_updates(node.companions_updated) or changed
        return changed

    def _apply_campaign_clock_updates(self, updates: dict[str, int]) -> bool:
        if self.campaign_progress is None or not updates:
            return False
        return self.campaign_progress.apply_clock_updates(updates)

    def _update_campaign_progress(self) -> bool:
        chapter = self._ensure_active_campaign_chapter()
        progress_state = self.campaign_progress
        campaign = self.campaign
        if chapter is None or progress_state is None or campaign is None:
            return False

        changed = progress_state.sync_clock_definitions(campaign)
        progress = progress_state.ensure_chapter_progress(
            chapter.id,
            started_turn=self.turn_count,
        )
        for milestone in chapter.milestones:
            if milestone.id in progress.completed_milestone_ids:
                continue
            if milestone.is_complete(
                story_flags=self.story_flags,
                objectives=self.objectives,
                turn_count=self.turn_count,
            ):
                progress.completed_milestone_ids.append(milestone.id)
                changed = True

        if chapter.milestones and len(progress.completed_milestone_ids) == len(chapter.milestones):
            if progress.completed_turn is None:
                progress.completed_turn = self.turn_count
                changed = True
            next_ref = campaign.next_chapter_ref(chapter.id)
            if next_ref is not None:
                next_act_id, next_chapter_id = next_ref
                if (
                    progress_state.active_act_id != next_act_id
                    or progress_state.active_chapter_id != next_chapter_id
                ):
                    progress_state.active_act_id = next_act_id
                    progress_state.active_chapter_id = next_chapter_id
                    progress_state.ensure_chapter_progress(
                        next_chapter_id,
                        started_turn=self.turn_count,
                    )
                    changed = True
            elif progress_state.completed_turn is None:
                progress_state.completed_turn = self.turn_count
                changed = True

        return changed

    def _ensure_active_campaign_chapter(self) -> CampaignChapter | None:
        if self.campaign is None:
            self.campaign_progress = None
            return None
        if (
            self.campaign_progress is None
            or self.campaign_progress.campaign_id != self.campaign.id
            or self.campaign.get_chapter_ref(self.campaign_progress.active_chapter_id) is None
        ):
            self.campaign_progress = CampaignProgress.from_campaign(
                self.campaign,
                started_turn=self.turn_count,
            )
        return self.campaign.get_chapter(self.campaign_progress.active_chapter_id)

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

    def _coerce_lore_entries(self, value: Any) -> list[LoreEntry]:
        if not isinstance(value, list):
            return []
        entries: list[LoreEntry] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                entries.append(LoreEntry(**item))
            except Exception:
                continue
        return entries

    def _coerce_companions(self, value: Any) -> list[Companion]:
        if not isinstance(value, list):
            return []
        companions: list[Companion] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                companions.append(Companion(**item))
            except Exception:
                continue
        return companions

    def _coerce_campaign(self, value: Any) -> CampaignPack | None:
        if isinstance(value, CampaignPack):
            return value.model_copy()
        if not isinstance(value, dict):
            return None
        try:
            return CampaignPack(**value)
        except Exception:
            return None

    def _coerce_campaign_progress(
        self,
        value: Any,
        *,
        campaign: CampaignPack | None,
    ) -> CampaignProgress | None:
        if campaign is None:
            return None
        if isinstance(value, CampaignProgress):
            progress = value.model_copy()
        elif isinstance(value, dict):
            try:
                progress = CampaignProgress(**value)
            except Exception:
                progress = None
        else:
            progress = None
        if progress is None or progress.campaign_id != campaign.id:
            return CampaignProgress.from_campaign(campaign, started_turn=self.turn_count)
        progress.sync_clock_definitions(campaign)
        return progress

    def _coerce_campaign_clocks(self, value: Any) -> list[CampaignClock]:
        if not isinstance(value, list):
            return []
        clocks: list[CampaignClock] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            try:
                clocks.append(CampaignClock(**item))
            except Exception:
                continue
        return clocks

    def _coerce_world_time(self, value: Any) -> WorldTime:
        if isinstance(value, WorldTime):
            return value.model_copy()
        if isinstance(value, dict):
            try:
                return WorldTime(**value)
            except Exception:
                return WorldTime()
        return WorldTime()

    def _coerce_snapshot_list(self, value: Any) -> list[GameStateSnapshot]:
        """Normalize saved undo/redo stacks."""
        if not isinstance(value, list):
            return []
        snapshots: list[GameStateSnapshot] = []
        for entry in value:
            snapshot = self._coerce_snapshot(entry)
            if snapshot is not None:
                snapshots.append(snapshot)
        return snapshots

    def _coerce_bookmarks(self, value: Any) -> dict[str, GameStateSnapshot]:
        """Normalize saved bookmark checkpoints."""
        if not isinstance(value, dict):
            return {}
        bookmarks: dict[str, GameStateSnapshot] = {}
        for name, snapshot in value.items():
            if not isinstance(name, str):
                continue
            normalized_snapshot = self._coerce_snapshot(snapshot)
            if normalized_snapshot is not None:
                bookmarks[name] = normalized_snapshot
        return bookmarks

    def _coerce_snapshot(self, value: Any) -> GameStateSnapshot | None:
        """Normalize a persisted snapshot payload into a typed memento."""
        if not isinstance(value, dict):
            return None

        node: StoryNode | None = None
        node_data = value.get("current_node")
        if isinstance(node_data, dict):
            try:
                node = StoryNode(**node_data)
            except Exception:
                node = None

        campaign = self._coerce_campaign(value.get("campaign"))
        campaign_progress = self._coerce_campaign_progress(
            value.get("campaign_progress"),
            campaign=campaign,
        )
        if campaign_progress is not None and not campaign_progress.clocks:
            campaign_progress.clocks = self._coerce_campaign_clocks(value.get("campaign_clocks"))

        return GameStateSnapshot(
            turn_count=self._coerce_positive_int(value.get("turn_count"), default=1),
            current_node=node,
            inventory=self._coerce_inventory(value.get("inventory")),
            player_stats=self._coerce_player_stats(value.get("player_stats")),
            story_title=value.get("story_title")
            if isinstance(value.get("story_title"), str)
            else None,
            current_scene_id=value.get("current_scene_id")
            if isinstance(value.get("current_scene_id"), str)
            else None,
            last_choice_text=value.get("last_choice_text")
            if isinstance(value.get("last_choice_text"), str)
            else None,
            last_choice_submission=value.get("last_choice_submission")
            if isinstance(value.get("last_choice_submission"), str)
            else (
                value.get("last_choice_text")
                if isinstance(value.get("last_choice_text"), str)
                else None
            ),
            timeline_metadata=self._coerce_timeline_metadata(value.get("timeline_metadata")),
            objectives=self._coerce_objectives(value.get("objectives")),
            faction_reputation=self._coerce_relationships(value.get("faction_reputation")),
            npc_affinity=self._coerce_relationships(value.get("npc_affinity")),
            story_flags=self._coerce_story_flags(value.get("story_flags")),
            lore_entries=self._coerce_lore_entries(value.get("lore_entries")),
            companions=self._coerce_companions(value.get("companions")),
            world_time=self._coerce_world_time(value.get("world_time")),
            campaign=campaign,
            campaign_progress=campaign_progress,
            campaign_clocks=(
                [clock.model_copy() for clock in campaign_progress.clocks]
                if campaign_progress is not None
                else []
            ),
            story_context=StoryContextMemento.from_payload(value.get("story_context_history")),
        )

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
