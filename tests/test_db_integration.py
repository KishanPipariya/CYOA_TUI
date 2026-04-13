from unittest.mock import MagicMock, patch

import pytest

from cyoa.db.graph_db import CYOAGraphDB


@pytest.fixture
def mock_neo4j():
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock:
        driver = mock.return_value
        session = MagicMock()
        driver.session.return_value.__enter__.return_value = session
        yield session


def test_db_create_story_node(mock_neo4j):
    # Mocking verify_connectivity to avoid failure in __init__
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j

        db = CYOAGraphDB(uri="bolt://test", user="u", password="p")

        # Mocking title check result (none existing)
        mock_neo4j.run.return_value = []

        title = db.create_story_node_and_get_title("New Adventure")

        assert title == "New Adventure"
        # Verify CREATE query
        # Correctly identifies if the second call to run was the CREATE
        create_call = mock_neo4j.run.call_args_list[1]
        query = create_call[0][0]
        assert "CREATE (s:Story" in query
        assert "$final_title" in query


def test_db_create_story_node_resolves_title_collisions(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j

        db = CYOAGraphDB(uri="bolt://test", user="u", password="p")
        mock_neo4j.run.side_effect = [
            [
                {"title": "New Adventure"},
                {"title": "New Adventure (2)"},
                {"title": "New Adventure (bad)"},
            ],
            MagicMock(),
        ]

        title = db.create_story_node_and_get_title("New Adventure")

        assert title == "New Adventure (3)"
        create_call = mock_neo4j.run.call_args_list[1]
        assert create_call.kwargs["final_title"] == "New Adventure (3)"


def test_db_create_scene_node(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j
        db = CYOAGraphDB()

        # Mocking CREATE result
        mock_result = MagicMock()
        mock_result.single.return_value = {"scene_id": "uuid-123"}
        mock_neo4j.run.return_value = mock_result

        scene_id = db.create_scene_node("Darkness...", ["Light lamp"], "Adventure 1")

        assert scene_id == "uuid-123"
        query = mock_neo4j.run.call_args[0][0]
        assert "CREATE (s:Scene" in query
        assert "BELONGS_TO" in query


def test_db_create_scene_node_with_mood(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j
        db = CYOAGraphDB()

        # Mocking CREATE result
        mock_result = MagicMock()
        mock_result.single.return_value = {"scene_id": "uuid-mood-123"}
        mock_neo4j.run.return_value = mock_result

        scene_id = db.create_scene_node(
            "Ethereal lights...", ["Touch them"], "Adventure 1", mood="ethereal"
        )

        assert scene_id == "uuid-mood-123"
        kwargs = mock_neo4j.run.call_args[1]
        assert kwargs["mood"] == "ethereal"
        query = mock_neo4j.run.call_args[0][0]
        assert "mood: $mood" in query


def test_db_create_choice_edge(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j
        db = CYOAGraphDB()

        db.create_choice_edge("src-id", "dst-id", "Go North")

        query = mock_neo4j.run.call_args[0][0]
        assert "MATCH (source:Scene {id: $source_id})" in query
        assert "CREATE (source)-[r:LEADS_TO" in query
        assert "action_text: $choice_text" in query


def test_offline_fallback():
    # Force connection failure
    with patch("cyoa.db.graph_db.GraphDatabase.driver", side_effect=Exception("Connection failed")):
        db = CYOAGraphDB()
        assert db.driver is None

        # These should return without error and provide defaults/UUIDs
        title = db.create_story_node_and_get_title("Offline Story")
        assert title == "Offline Story"

        scene_id = db.create_scene_node("Narrative", [], "Story")
        assert len(scene_id) > 10  # Should be a UUID string


def test_get_story_tree_prunes_cycles(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j
        db = CYOAGraphDB(uri="bolt://test", user="u", password="p")

        mock_neo4j.run.return_value = [
            {
                "id": "root",
                "narrative": "Start",
                "mood": "default",
                "next_id": "mid",
                "choice": "Go forward",
            },
            {
                "id": "mid",
                "narrative": "Middle",
                "mood": "combat",
                "next_id": "root",
                "choice": "Loop back",
            },
        ]

        tree = db.get_story_tree("Adventure")

        assert tree["root_id"] == "root"
        assert tree["edges"]["root"] == [{"target_id": "mid", "choice": "Go forward"}]
        assert tree["edges"]["mid"] == []


def test_get_story_tree_uses_fallback_root_when_every_node_has_incoming(mock_neo4j):
    with patch("cyoa.db.graph_db.GraphDatabase.driver") as mock_driver_call:
        mock_driver_call.return_value.session.return_value.__enter__.return_value = mock_neo4j
        db = CYOAGraphDB(uri="bolt://test", user="u", password="p")

        mock_neo4j.run.return_value = [
            {
                "id": "a",
                "narrative": "A",
                "mood": "default",
                "next_id": "b",
                "choice": "A to B",
            },
            {
                "id": "b",
                "narrative": "B",
                "mood": "heroic",
                "next_id": "a",
                "choice": "B to A",
            },
        ]

        tree = db.get_story_tree("Adventure")

        assert tree["root_id"] == "a"
        assert tree["edges"]["a"] == [{"target_id": "b", "choice": "A to B"}]
        assert tree["edges"]["b"] == []
