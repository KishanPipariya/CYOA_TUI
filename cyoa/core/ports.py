from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol


class StoryRepository(Protocol):
    async def save_scene_async(
        self,
        *,
        narrative: str,
        available_choices: list[str],
        story_title: str,
        source_scene_id: str | None,
        choice_text: str | None,
        player_stats: dict[str, int],
        inventory: list[str],
        mood: str,
    ) -> str: ...

    def create_story_node_and_get_title(self, generated_title: str) -> str: ...
    def get_scene_history_path(
        self, current_scene_id: str, max_depth: int = 100
    ) -> dict[str, Any] | None: ...
    def get_story_tree(self, story_title: str) -> dict[str, Any]: ...
    async def verify_connectivity_async(self) -> bool: ...
    def close(self) -> None: ...


class NarrativeMemoryStore(Protocol):
    @property
    def is_online(self) -> bool: ...

    async def add_async(self, scene_id: str, narrative: str) -> None: ...
    async def query_async(self, text: str, n: int = 3) -> list[str]: ...
    async def get_recent_async(self, n: int = 2, *, exclude_text: str | None = None) -> list[str]: ...
    def close(self) -> None: ...


class NPCMemoryStore(Protocol):
    @property
    def is_online(self) -> bool: ...

    async def add_async(self, npc_name: str, scene_id: str, narrative: str) -> None: ...
    async def query_async(self, npc_name: str, text: str, n: int = 2) -> list[str]: ...
    def close(self) -> None: ...


NarrativeMemoryFactory = Callable[[], NarrativeMemoryStore]
NPCMemoryFactory = Callable[[], NPCMemoryStore]
