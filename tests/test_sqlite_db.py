import asyncio
import sqlite3

import pytest

from cyoa.db.sqlite_db import CYOASQLiteDB, SQLitePersistenceError


def _create_db(tmp_path) -> CYOASQLiteDB:
    return CYOASQLiteDB(tmp_path / "stories.sqlite3")


def test_initialization_creates_schema_with_local_sqlite_settings(tmp_path) -> None:
    path = tmp_path / "stories.sqlite3"
    _create_db(tmp_path)

    assert path.exists()
    with sqlite3.connect(path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
    assert {"stories", "scenes", "scene_edges"} <= tables
    assert journal_mode == "wal"


def test_story_title_collisions_receive_suffixes(tmp_path) -> None:
    db = _create_db(tmp_path)

    assert db.create_story_node_and_get_title("Adventure") == "Adventure"
    assert db.create_story_node_and_get_title("Adventure") == "Adventure (2)"
    assert db.create_story_node_and_get_title("Adventure") == "Adventure (3)"


@pytest.mark.asyncio
async def test_save_scene_persists_edge_and_serialized_state_atomically(tmp_path) -> None:
    db = _create_db(tmp_path)
    title = db.create_story_node_and_get_title("Adventure")
    root = await db.save_scene_async(
        narrative="Root",
        available_choices=["Go north"],
        story_title=title,
        source_scene_id=None,
        choice_text=None,
        player_stats={"health": 85},
        inventory=["Torch"],
        mood="tense",
        lore_entries=[{"name": "Keep"}],
        world_time={"day": 2},
    )
    child = await db.save_scene_async(
        narrative="North",
        available_choices=[],
        story_title=title,
        source_scene_id=root,
        choice_text="Go north",
        player_stats={"gold": 9},
        inventory=["Torch", "Key"],
        mood="calm",
        lore_entries=[],
        world_time={},
    )

    history = db.get_scene_history_path(child)
    assert history == {
        "scenes": [
            {
                "id": root,
                "narrative": "Root",
                "available_choices": ["Go north"],
                "player_stats": {"health": 85, "gold": 0, "reputation": 0},
                "inventory": ["Torch"],
                "lore_entries": [{"name": "Keep"}],
                "world_time": {"day": 2},
            },
            {
                "id": child,
                "narrative": "North",
                "available_choices": [],
                "player_stats": {"health": 100, "gold": 9, "reputation": 0},
                "inventory": ["Torch", "Key"],
                "lore_entries": [],
            },
        ],
        "choices": ["Go north"],
    }


@pytest.mark.asyncio
async def test_failed_edge_write_rolls_back_scene(tmp_path) -> None:
    db = _create_db(tmp_path)
    title = db.create_story_node_and_get_title("Adventure")

    with pytest.raises(SQLitePersistenceError, match="source scene"):
        await db.save_scene_async(
            narrative="Orphan",
            available_choices=[],
            story_title=title,
            source_scene_id="missing",
            choice_text="Go",
            player_stats={},
            inventory=[],
            mood="default",
        )

    assert db.get_story_tree(title) == {}


@pytest.mark.asyncio
async def test_story_tree_contains_complete_branching_structure(tmp_path) -> None:
    db = _create_db(tmp_path)
    title = db.create_story_node_and_get_title("Adventure")
    root = await db.save_scene_async(
        narrative="Root",
        available_choices=[],
        story_title=title,
        source_scene_id=None,
        choice_text=None,
        player_stats={},
        inventory=[],
        mood="default",
    )
    north, south = await asyncio.gather(
        db.save_scene_async(
            narrative="North",
            available_choices=[],
            story_title=title,
            source_scene_id=root,
            choice_text="North",
            player_stats={},
            inventory=[],
            mood="cold",
        ),
        db.save_scene_async(
            narrative="South",
            available_choices=[],
            story_title=title,
            source_scene_id=root,
            choice_text="South",
            player_stats={},
            inventory=[],
            mood="warm",
        ),
    )

    tree = db.get_story_tree(title)
    assert tree["root_id"] == root
    assert tree["nodes"][north]["mood"] == "cold"
    assert tree["edges"][root] == [
        {"target_id": north, "choice": "North"},
        {"target_id": south, "choice": "South"},
    ]


def test_close_prevents_new_database_operations(tmp_path) -> None:
    db = _create_db(tmp_path)
    db.close()

    with pytest.raises(SQLitePersistenceError, match="closed"):
        db.create_story_node_and_get_title("Adventure")
