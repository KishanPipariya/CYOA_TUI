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
