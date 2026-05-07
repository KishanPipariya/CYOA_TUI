import json
from unittest.mock import MagicMock

import pytest

from cyoa.db import graph_db
from cyoa.db.graph_db import CYOAGraphDB


def _online_db(
    monkeypatch: pytest.MonkeyPatch, session: MagicMock
) -> tuple[CYOAGraphDB, MagicMock]:
    driver = MagicMock()
    driver.session.return_value.__enter__.return_value = session
    graph_database = MagicMock()
    graph_database.driver.return_value = driver
    monkeypatch.setattr(graph_db, "_NEO4J_AVAILABLE", True)
    monkeypatch.setattr(graph_db, "GraphDatabase", graph_database)

    db = CYOAGraphDB(uri="bolt://test", user="neo4j", password="secret")

    graph_database.driver.assert_called_once_with(
        "bolt://test",
        auth=("neo4j", "secret"),
        connection_timeout=1.0,
    )
    return db, driver


def test_create_story_node_resolves_collisions_without_neo4j_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.run.side_effect = [
        [
            {"title": "Adventure"},
            {"title": "Adventure (2)"},
            {"title": "Adventure (bad)"},
        ],
        MagicMock(),
    ]
    db, _driver = _online_db(monkeypatch, session)

    title = db.create_story_node_and_get_title("Adventure")

    assert title == "Adventure (3)"
    assert session.run.call_args_list[0].kwargs == {"base_title": "Adventure"}
    assert session.run.call_args_list[1].kwargs["final_title"] == "Adventure (3)"


def test_create_scene_node_writes_serialized_state_without_neo4j_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    result = MagicMock()
    result.single.return_value = {"scene_id": "scene-42"}
    session.run.return_value = result
    db, _driver = _online_db(monkeypatch, session)

    scene_id = db.create_scene_node(
        "The archive door opens.",
        ["Enter", "Wait"],
        "Adventure",
        player_stats={"health": 75, "gold": 12},
        inventory=["key"],
        mood="tense",
        lore_entries=[{"name": "Archive", "detail": "sealed"}],
        world_time={"day": 3, "hour": 21},
    )

    assert scene_id == "scene-42"
    kwargs = session.run.call_args.kwargs
    assert kwargs["player_health"] == 75
    assert kwargs["player_gold"] == 12
    assert kwargs["player_reputation"] == 0
    assert kwargs["inventory"] == ["key"]
    assert kwargs["mood"] == "tense"
    assert json.loads(kwargs["lore_entries_json"]) == [{"name": "Archive", "detail": "sealed"}]
    assert json.loads(kwargs["world_time_json"]) == {"day": 3, "hour": 21}


@pytest.mark.asyncio
async def test_save_scene_async_links_source_scene_when_choice_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    result = MagicMock()
    result.single.return_value = {"scene_id": "scene-new"}
    session.run.side_effect = [result, MagicMock()]
    db, _driver = _online_db(monkeypatch, session)

    scene_id = await db.save_scene_async(
        "A bridge lowers.",
        ["Cross"],
        "Adventure",
        source_scene_id="scene-old",
        choice_text="Pull the lever",
    )

    assert scene_id == "scene-new"
    edge_kwargs = session.run.call_args_list[1].kwargs
    assert edge_kwargs == {
        "source_id": "scene-old",
        "target_id": "scene-new",
        "choice_text": "Pull the lever",
    }


def test_get_scene_history_path_deserializes_scene_state_without_neo4j_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    result = MagicMock()
    result.single.return_value = {
        "scenes": [
            {
                "id": "root",
                "narrative": "Start",
                "available_choices": ["Go"],
                "player_health": 80,
                "player_gold": 4,
                "inventory": ["map"],
                "lore_entries_json": '[{"name": "Map"}]',
                "world_time_json": '{"day": 2}',
            },
            {
                "id": "current",
                "narrative": "Arrive",
                "available_choices": [],
                "player_reputation": 9,
                "inventory": [],
                "lore_entries_json": "not json",
                "world_time_json": "[]",
            },
        ],
        "choices": [{"action_text": "Go"}],
    }
    session.run.return_value = result
    db, _driver = _online_db(monkeypatch, session)

    history = db.get_scene_history_path("current", max_depth=7)

    assert history == {
        "scenes": [
            {
                "id": "root",
                "narrative": "Start",
                "available_choices": ["Go"],
                "player_stats": {"health": 80, "gold": 4, "reputation": 0},
                "inventory": ["map"],
                "lore_entries": [{"name": "Map"}],
                "world_time": {"day": 2},
            },
            {
                "id": "current",
                "narrative": "Arrive",
                "available_choices": [],
                "player_stats": {"health": 100, "gold": 0, "reputation": 9},
                "inventory": [],
                "lore_entries": [],
            },
        ],
        "choices": ["Go"],
    }
    query = session.run.call_args.args[0]
    assert "LEADS_TO*..7" in query
    assert session.run.call_args.kwargs == {"current_id": "current"}


def test_story_queries_build_linear_path_and_tree_without_neo4j_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    records = [
        {"id": "root", "narrative": "Start", "mood": "calm", "next_id": "left", "choice": "Left"},
        {
            "id": "root",
            "narrative": "Start",
            "mood": "calm",
            "next_id": "right",
            "choice": "Right",
        },
        {"id": "left", "narrative": "Left path", "mood": "tense", "next_id": None, "choice": None},
        {
            "id": "right",
            "narrative": "Right path",
            "mood": "bright",
            "next_id": None,
            "choice": None,
        },
    ]
    session.run.side_effect = [records, records]
    db, _driver = _online_db(monkeypatch, session)

    path = db.get_all_story_scenes("Adventure")
    tree = db.get_story_tree("Adventure")

    assert path == [
        {"id": "root", "narrative": "Start", "choice_taken": "Left"},
        {"id": "left", "narrative": "Left path", "choice_taken": None},
    ]
    assert tree["root_id"] == "root"
    assert tree["nodes"]["right"]["mood"] == "bright"
    assert tree["edges"]["root"] == [
        {"target_id": "left", "choice": "Left"},
        {"target_id": "right", "choice": "Right"},
    ]


def test_graph_db_offline_paths_and_serializers_are_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CYOA_ENABLE_GRAPH_DB", raising=False)
    monkeypatch.delenv("NEO4J_URI", raising=False)
    monkeypatch.delenv("NEO4J_USER", raising=False)
    monkeypatch.delenv("NEO4J_PASSWORD", raising=False)

    db = CYOAGraphDB()

    assert db.create_story_node_and_get_title("Offline") == "Offline"
    assert db.get_scene_history_path("missing") is None
    assert db.get_all_story_scenes("Offline") == []
    assert db.get_story_tree("Offline") == {}
    assert CYOAGraphDB._deserialize_lore_entries('[{"valid": true}, "skip"]') == [{"valid": True}]
    assert CYOAGraphDB._deserialize_lore_entries("{}") == []
    assert CYOAGraphDB._deserialize_world_time('{"day": 1}') == {"day": 1}
    assert CYOAGraphDB._deserialize_world_time("not json") == {}
