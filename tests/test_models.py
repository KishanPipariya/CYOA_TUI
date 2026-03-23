import pytest
from pydantic import ValidationError
from cyoa.core.models import Choice, StoryNode

def test_choice_basic_valid():
    choice = Choice(text="Take the sword")
    assert choice.text == "Take the sword"

def test_choice_invalid_type():
    with pytest.raises(ValidationError):
        Choice(text=123)

def test_story_node_basic_valid():
    node = StoryNode(
        narrative="You wake up in a damp cell.",
        choices=[Choice(text="Look around"), Choice(text="Shout for help")]
    )
    assert node.narrative == "You wake up in a damp cell."
    assert len(node.choices) == 2
    assert node.is_ending is False
    assert node.items_gained == []
    assert node.stat_updates == {}

def test_story_node_default_values():
    node = StoryNode(
        narrative="End",
        choices=[],
        is_ending=True
    )
    assert node.is_ending is True
    assert node.items_lost == []
    assert node.npcs_present == []

def test_story_node_missing_required_fields():
    # Narrative is required
    with pytest.raises(ValidationError):
        StoryNode(choices=[Choice(text="A"), Choice(text="B")])
    # Choices is required
    with pytest.raises(ValidationError):
        StoryNode(narrative="X")

def test_story_node_invalid_stat_updates():
    with pytest.raises(ValidationError):
        StoryNode(
            narrative="X",
            choices=[Choice(text="A"), Choice(text="B")],
            stat_updates={"health": "not an int"}
        )

def test_story_node_choice_validation_enforcement():
    """Verify if choice count limits (2-4) are enforced for non-ending nodes."""
    # Too few choices
    with pytest.raises(ValidationError):
        StoryNode(
            narrative="X",
            choices=[Choice(text="Only one")],
            is_ending=False
        )

    # Too many choices
    with pytest.raises(ValidationError):
        StoryNode(
            narrative="X",
            choices=[Choice(text="1"), Choice(text="2"), Choice(text="3"), Choice(text="4"), Choice(text="5")],
            is_ending=False
        )

def test_story_node_ending_with_no_choices():
    """Verify that ending nodes can have zero choices."""
    node = StoryNode(
        narrative="The End.",
        choices=[],
        is_ending=True
    )
    assert node.choices == []
