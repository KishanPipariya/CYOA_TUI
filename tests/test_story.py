"""
Automated Story Tests — tests/test_story.py

Headless test harness that verifies core CYOA behaviour without loading the
actual LLM model or requiring a Neo4j instance.
"""
import pytest
from unittest.mock import patch, MagicMock

from models import StoryNode, Choice
from llm_backend import StoryContext, MAX_CONTEXT_TURNS
from graph_db import CYOAGraphDB
from theme_loader import load_theme, list_themes


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_node(narrative: str = "You are in a dungeon.", n_choices: int = 2, is_ending: bool = False) -> StoryNode:
    choices = [Choice(text=f"Choice {i + 1}") for i in range(n_choices)]
    return StoryNode(narrative=narrative, choices=choices, is_ending=is_ending)


# ── 1. Context window sliding window ─────────────────────────────────────────

class TestStoryContext:
    def test_history_within_max_turns(self):
        """History should never exceed (system + initial_prompt + max_turns*2) messages."""
        ctx = StoryContext(starting_prompt="Start", max_turns=3)
        for i in range(10):
            ctx.add_turn(f"Narrative {i}", f"Choice {i}")

        # system + initial user + 3 turn pairs = 2 + 6 = 8
        assert len(ctx.history) == 8

    def test_system_and_initial_prompt_preserved(self):
        """System message and starting prompt must always remain."""
        ctx = StoryContext(starting_prompt="My prompt", max_turns=2)
        for i in range(8):
            ctx.add_turn("narrative", "choice")

        assert ctx.history[0]["role"] == "system"
        assert ctx.history[1]["content"] == "My prompt"

    def test_no_trim_when_under_limit(self):
        """History should not trim if turns are within the window."""
        ctx = StoryContext(starting_prompt="Start", max_turns=5)
        ctx.add_turn("n1", "c1")
        ctx.add_turn("n2", "c2")
        # 2 + 2*2 = 6 messages — under limit of 2 + 5*2 = 12
        assert len(ctx.history) == 6


# ── 2. LLM JSON parse failure graceful fallback ───────────────────────────────

class TestStoryGeneratorFallback:
    def test_bad_json_returns_fallback_node(self):
        """If LLM returns invalid JSON, generate_next_node should return a valid fallback StoryNode."""
        from llm_backend import StoryGenerator

        with patch("llm_backend.Llama") as MockLlama:
            mock_llm = MagicMock()
            mock_llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": "NOT VALID JSON {"}}]
            }
            MockLlama.return_value = mock_llm

            gen = StoryGenerator.__new__(StoryGenerator)
            gen.llm = mock_llm
            gen._schema = StoryNode.model_json_schema()
            gen._temperature = 0.6
            gen._max_tokens = 512

            ctx = StoryContext("start")
            node = gen.generate_next_node(ctx)

        assert isinstance(node, StoryNode)
        assert len(node.choices) >= 1  # fallback always has a choice

    def test_valid_json_returns_parsed_node(self):
        """If LLM returns valid JSON, generate_next_node should return a proper StoryNode."""
        import json
        from llm_backend import StoryGenerator

        payload = StoryNode(
            narrative="A torch flickers.",
            choices=[Choice(text="Pick it up"), Choice(text="Leave it")]
        ).model_dump()

        with patch("llm_backend.Llama") as MockLlama:
            mock_llm = MagicMock()
            mock_llm.create_chat_completion.return_value = {
                "choices": [{"message": {"content": json.dumps(payload)}}]
            }
            MockLlama.return_value = mock_llm

            gen = StoryGenerator.__new__(StoryGenerator)
            gen.llm = mock_llm
            gen._schema = StoryNode.model_json_schema()
            gen._temperature = 0.6
            gen._max_tokens = 512

            ctx = StoryContext("start")
            node = gen.generate_next_node(ctx)

        assert node.narrative == "A torch flickers."
        assert len(node.choices) == 2


# ── 3. Graph DB offline graceful degradation ──────────────────────────────────

class TestCYOAGraphDBOffline:
    def test_offline_sets_driver_none(self):
        """CYOAGraphDB with unreachable URI should set driver=None without raising."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")  # nothing listening here
        assert db.driver is None

    def test_offline_create_scene_returns_uuid(self):
        """create_scene_node with no driver should return a UUID string without crashing."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        scene_id = db.create_scene_node("narrative", ["choice"], "My Story")
        assert isinstance(scene_id, str) and len(scene_id) == 36  # UUID format

    def test_offline_create_edge_is_noop(self):
        """create_choice_edge with no driver should return silently."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        db.create_choice_edge("id-a", "id-b", "Go north")  # should not raise

    def test_offline_story_title_passthrough(self):
        """create_story_node_and_get_title with no driver should return the input title unchanged."""
        db = CYOAGraphDB(uri="bolt://localhost:9999")
        result = db.create_story_node_and_get_title("My Adventure")
        assert result == "My Adventure"


# ── 4. is_ending propagation ──────────────────────────────────────────────────

class TestStoryNodeEnding:
    def test_is_ending_defaults_false(self):
        node = _make_node()
        assert node.is_ending is False

    def test_is_ending_true_with_empty_choices(self):
        node = StoryNode(narrative="You have escaped!", choices=[], is_ending=True)
        assert node.is_ending is True
        assert len(node.choices) == 0

    def test_is_ending_false_with_choices(self):
        node = _make_node(n_choices=3)
        assert node.is_ending is False
        assert len(node.choices) == 3


# ── 5. Theme loading ──────────────────────────────────────────────────────────

class TestThemeLoader:
    def test_load_dark_dungeon(self):
        theme = load_theme("dark_dungeon")
        assert "prompt" in theme
        assert "name" in theme
        assert len(theme["prompt"]) > 10

    def test_load_space_explorer(self):
        theme = load_theme("space_explorer")
        assert "prompt" in theme
        assert "name" in theme

    def test_invalid_theme_raises(self):
        with pytest.raises(FileNotFoundError, match="not found"):
            load_theme("nonexistent_theme_xyz")

    def test_list_themes_includes_defaults(self):
        themes = list_themes()
        assert "dark_dungeon" in themes
        assert "space_explorer" in themes
