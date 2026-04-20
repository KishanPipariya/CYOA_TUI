from pathlib import Path

from cyoa.core.events import Events, bus
from cyoa.core.models import Choice, StoryNode
from cyoa.db.story_logger import StoryLogger


def test_story_logger_records_live_story_transcript(tmp_path: Path) -> None:
    log_path = tmp_path / "logs" / "story.md"
    logger = StoryLogger(filepath=str(log_path))

    try:
        bus.emit(Events.STORY_TITLE_GENERATED, title="Test Adventure")
        bus.emit(
            Events.NODE_COMPLETED,
            node=StoryNode(
                narrative="The cell door groans open.",
                choices=[Choice(text="Run"), Choice(text="Hide")],
            ),
        )
        bus.emit(Events.CHOICE_MADE, choice_text="Run")
        bus.emit(
            Events.NODE_COMPLETED,
            node=StoryNode(
                narrative="You sprint into the corridor.",
                choices=[Choice(text="Left"), Choice(text="Right")],
            ),
        )
    finally:
        logger.close()

    assert log_path.read_text(encoding="utf-8") == (
        "# Test Adventure\n\n"
        "The cell door groans open.\n\n"
        "> **You chose:** Run\n\n---\n\n"
        "You sprint into the corridor.\n\n"
    )


def test_story_logger_unsubscribes_on_close(tmp_path: Path) -> None:
    log_path = tmp_path / "story.md"
    logger = StoryLogger(filepath=str(log_path))
    logger.close()

    bus.emit(Events.STORY_TITLE_GENERATED, title="Ignored")

    assert not log_path.exists()
