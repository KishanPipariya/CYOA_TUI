"""Local SQLite persistence for story scenes and their branching history."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypedDict

from cyoa.core.constants import get_user_data_dir
from cyoa.core.observability import DBObservedSession


class SQLitePersistenceError(RuntimeError):
    """Raised when the local story database cannot complete an operation."""


class StoryTreeEdge(TypedDict):
    target_id: str
    choice: str | None


class StoryTreeNode(TypedDict):
    id: str
    narrative: str
    mood: str


class CYOASQLiteDB:
    """A single-user, local repository backed by short-lived SQLite connections."""

    _SCHEMA = """
        CREATE TABLE IF NOT EXISTS stories (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL UNIQUE
        );
        CREATE TABLE IF NOT EXISTS scenes (
            id TEXT PRIMARY KEY,
            story_id TEXT NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
            narrative TEXT NOT NULL,
            available_choices TEXT NOT NULL,
            player_stats TEXT NOT NULL,
            inventory TEXT NOT NULL,
            lore_entries TEXT NOT NULL,
            world_time TEXT NOT NULL,
            mood TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS scene_edges (
            source_scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
            target_scene_id TEXT NOT NULL REFERENCES scenes(id) ON DELETE CASCADE,
            action_text TEXT NOT NULL,
            PRIMARY KEY (source_scene_id, target_scene_id)
        );
        CREATE INDEX IF NOT EXISTS idx_scenes_story_id ON scenes(story_id);
        CREATE INDEX IF NOT EXISTS idx_scene_edges_source ON scene_edges(source_scene_id);
        CREATE INDEX IF NOT EXISTS idx_scene_edges_target ON scene_edges(target_scene_id);
    """

    def __init__(self, database_path: str | Path | None = None) -> None:
        default_path = get_user_data_dir() / "stories.sqlite3"
        self.database_path = Path(database_path or default_path).expanduser()
        self._closed = False
        try:
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise SQLitePersistenceError(
                f"Unable to create story database directory {self.database_path.parent}: {exc}"
            ) from exc
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise SQLitePersistenceError("The SQLite story database has been closed.")
        try:
            connection = sqlite3.connect(self.database_path, timeout=10.0)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            return connection
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"Unable to open story database {self.database_path}: {exc}"
            ) from exc

    def _initialize_schema(self) -> None:
        try:
            with self._connect() as connection:
                connection.executescript(self._SCHEMA)
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(
                f"Unable to initialize story database {self.database_path}: {exc}"
            ) from exc

    @staticmethod
    def _normalize_player_stats(player_stats: dict[str, int] | None) -> dict[str, int]:
        base_stats = {"health": 100, "gold": 0, "reputation": 0}
        if player_stats:
            base_stats.update(player_stats)
        return base_stats

    @staticmethod
    def _parse_title_modifier(existing_title: str, base_title: str) -> int | None:
        if existing_title == base_title:
            return 1
        prefix = f"{base_title} ("
        if not existing_title.startswith(prefix) or not existing_title.endswith(")"):
            return None
        try:
            return int(existing_title[len(prefix) : -1])
        except ValueError:
            return None

    @classmethod
    def _resolve_story_title_collision(cls, base_title: str, existing_titles: Iterable[str]) -> str:
        highest_modifier = max(
            (
                modifier
                for modifier in (
                    cls._parse_title_modifier(title, base_title) for title in existing_titles
                )
                if modifier is not None
            ),
            default=0,
        )
        return base_title if highest_modifier == 0 else f"{base_title} ({highest_modifier + 1})"

    def create_story_node_and_get_title(self, generated_title: str) -> str:
        """Create a story with a collision-free title and return that title."""
        try:
            with (
                DBObservedSession("sqlite", "create_story") as session_obs,
                self._connect() as connection,
            ):
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute(
                    "SELECT title FROM stories WHERE title = ? OR substr(title, 1, ?) = ?",
                    (generated_title, len(generated_title) + 2, f"{generated_title} ("),
                )
                title = self._resolve_story_title_collision(
                    generated_title, (row[0] for row in rows)
                )
                connection.execute(
                    "INSERT INTO stories (id, title) VALUES (?, ?)", (str(uuid.uuid4()), title)
                )
                if session_obs.span:
                    session_obs.span.set_attribute("story.title_length", len(title))
                return title
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(f"Unable to create story: {exc}") from exc

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
        lore_entries: list[dict[str, Any]] | None = None,
        world_time: dict[str, Any] | None = None,
    ) -> str:
        """Atomically persist a scene and its incoming choice edge in a worker thread."""
        return await asyncio.to_thread(
            self._save_scene,
            narrative,
            available_choices,
            story_title,
            source_scene_id,
            choice_text,
            player_stats,
            inventory,
            mood,
            lore_entries,
            world_time,
        )

    def _save_scene(
        self,
        narrative: str,
        available_choices: list[str],
        story_title: str,
        source_scene_id: str | None,
        choice_text: str | None,
        player_stats: dict[str, int],
        inventory: list[str],
        mood: str,
        lore_entries: list[dict[str, Any]] | None,
        world_time: dict[str, Any] | None,
    ) -> str:
        scene_id = str(uuid.uuid4())
        try:
            with (
                DBObservedSession("sqlite", "save_scene") as session_obs,
                self._connect() as connection,
            ):
                connection.execute("BEGIN IMMEDIATE")
                story = connection.execute(
                    "SELECT id FROM stories WHERE title = ?", (story_title,)
                ).fetchone()
                if story is None:
                    raise SQLitePersistenceError(
                        f"Story {story_title!r} does not exist in the local database."
                    )
                connection.execute(
                    """INSERT INTO scenes (
                        id, story_id, narrative, available_choices, player_stats, inventory,
                        lore_entries, world_time, mood
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        scene_id,
                        story[0],
                        narrative,
                        json.dumps(available_choices),
                        json.dumps(self._normalize_player_stats(player_stats)),
                        json.dumps(inventory),
                        json.dumps(lore_entries or []),
                        json.dumps(world_time or {}),
                        mood,
                    ),
                )
                if source_scene_id is not None or choice_text is not None:
                    if not source_scene_id or choice_text is None:
                        raise SQLitePersistenceError(
                            "A scene edge requires both a source scene and a choice."
                        )
                    source = connection.execute(
                        "SELECT story_id FROM scenes WHERE id = ?", (source_scene_id,)
                    ).fetchone()
                    if source is None or source[0] != story[0]:
                        raise SQLitePersistenceError(
                            "The source scene does not belong to this story."
                        )
                    connection.execute(
                        "INSERT INTO scene_edges (source_scene_id, target_scene_id, action_text) VALUES (?, ?, ?)",
                        (source_scene_id, scene_id, choice_text),
                    )
                if session_obs.span:
                    session_obs.span.set_attribute("scene.id", scene_id)
                return scene_id
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(f"Unable to save scene: {exc}") from exc

    def get_scene_history_path(
        self, current_scene_id: str, max_depth: int = 100
    ) -> dict[str, Any] | None:
        """Return the root-to-current path, including state serialized for each scene."""
        if max_depth < 0:
            raise ValueError("max_depth must not be negative")
        try:
            with DBObservedSession("sqlite", "get_scene_history"), self._connect() as connection:
                rows = connection.execute(
                    """WITH RECURSIVE path(id, depth, incoming_choice) AS (
                        SELECT ?, 0, NULL
                        UNION ALL
                        SELECT edge.source_scene_id, path.depth + 1, edge.action_text
                        FROM scene_edges AS edge JOIN path ON edge.target_scene_id = path.id
                        WHERE path.depth < ?
                    )
                    SELECT path.depth, path.incoming_choice, scenes.id, scenes.narrative,
                           scenes.available_choices, scenes.player_stats, scenes.inventory,
                           scenes.lore_entries, scenes.world_time
                    FROM path JOIN scenes ON scenes.id = path.id
                    ORDER BY path.depth DESC""",
                    (current_scene_id, max_depth),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(f"Unable to retrieve scene history: {exc}") from exc
        if not rows:
            return None
        scenes: list[dict[str, Any]] = []
        choices: list[str] = []
        for (
            _depth,
            incoming_choice,
            scene_id,
            narrative,
            raw_choices,
            raw_stats,
            raw_inventory,
            raw_lore,
            raw_time,
        ) in rows:
            scenes.append(
                {
                    "id": scene_id,
                    "narrative": narrative,
                    "available_choices": json.loads(raw_choices),
                    "player_stats": json.loads(raw_stats),
                    "inventory": json.loads(raw_inventory),
                    "lore_entries": json.loads(raw_lore),
                    **({"world_time": json.loads(raw_time)} if json.loads(raw_time) else {}),
                }
            )
            if incoming_choice is not None:
                choices.append(incoming_choice)
        return {"scenes": scenes, "choices": choices}

    @staticmethod
    def _pick_story_root(nodes: dict[str, StoryTreeNode], has_incoming: set[str]) -> str | None:
        return next(
            (node_id for node_id in nodes if node_id not in has_incoming), next(iter(nodes), None)
        )

    def get_story_tree(self, story_title: str) -> dict[str, Any]:
        """Return every scene and reachable branch edge for a story map."""
        try:
            with DBObservedSession("sqlite", "get_story_tree"), self._connect() as connection:
                scene_rows = connection.execute(
                    """SELECT scenes.id, scenes.narrative, scenes.mood
                    FROM scenes JOIN stories ON stories.id = scenes.story_id
                    WHERE stories.title = ? ORDER BY scenes.id""",
                    (story_title,),
                ).fetchall()
                edge_rows = connection.execute(
                    """SELECT edge.source_scene_id, edge.target_scene_id, edge.action_text
                    FROM scene_edges AS edge
                    JOIN scenes AS source ON source.id = edge.source_scene_id
                    JOIN scenes AS target ON target.id = edge.target_scene_id
                    JOIN stories ON stories.id = source.story_id AND target.story_id = stories.id
                    WHERE stories.title = ? ORDER BY edge.action_text, edge.target_scene_id""",
                    (story_title,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise SQLitePersistenceError(f"Unable to retrieve story tree: {exc}") from exc
        if not scene_rows:
            return {}
        nodes: dict[str, StoryTreeNode] = {
            scene_id: {"id": scene_id, "narrative": narrative, "mood": mood}
            for scene_id, narrative, mood in scene_rows
        }
        raw_edges: dict[str, list[StoryTreeEdge]] = {scene_id: [] for scene_id in nodes}
        has_incoming: set[str] = set()
        for source_id, target_id, action_text in edge_rows:
            raw_edges[source_id].append({"target_id": target_id, "choice": action_text})
            has_incoming.add(target_id)
        root_id = self._pick_story_root(nodes, has_incoming)
        assert root_id is not None
        edges: dict[str, list[StoryTreeEdge]] = {scene_id: [] for scene_id in nodes}

        def walk(scene_id: str, active_path: set[str]) -> None:
            active_path.add(scene_id)
            for edge in raw_edges[scene_id]:
                if edge["target_id"] not in active_path:
                    edges[scene_id].append(edge)
                    walk(edge["target_id"], active_path)
            active_path.remove(scene_id)

        walk(root_id, set())
        return {"root_id": root_id, "nodes": nodes, "edges": edges}

    def close(self) -> None:
        """Prevent further operations; connections are already closed after each operation."""
        self._closed = True
