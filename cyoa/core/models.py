from pydantic import BaseModel, Field, model_validator  # type: ignore
from typing import List, Optional, Dict


class Choice(BaseModel):
    text: str = Field(description="The description of the action the user can take.")


class StoryNode(BaseModel):
    narrative: str = Field(
        description="The unfolding story text describing what just happened and the current situation."
    )
    title: Optional[str] = Field(
        default=None,
        description="The generated title for this story adventure. (Only necessary for the very first node of the game).",
    )
    items_gained: List[str] = Field(
        default_factory=list,
        description="Items the player just picked up or earned in this turn. Only list NEW items. If none, return [].",
    )
    items_lost: List[str] = Field(
        default_factory=list,
        description="Items the player just used, dropped, or lost in this turn. If none, return [].",
    )
    npcs_present: List[str] = Field(
        default_factory=list,
        description="A list of named NPCs present in the current scene. If none, return [].",
    )
    stat_updates: Dict[str, int] = Field(
        default_factory=dict,
        description="Updates to the player's stats (health, gold, reputation). E.g. {'health': -10, 'gold': 50}. Only include changes.",
    )
    choices: List[Choice] = Field(
        description="A list of 2 to 4 choices for the user's next action.",
        json_schema_extra={"minItems": 2, "maxItems": 4},
    )
    is_ending: bool = Field(
        default=False,
        description="Set to true if this narrative is a definitive ending to the story (victory, death, etc). If true, choices may be empty.",
    )

    @model_validator(mode="after")
    def validate_choices_count(self) -> "StoryNode":
        if not self.is_ending:
            if not (2 <= len(self.choices) <= 4):
                raise ValueError(
                    f"Non-ending narrative must have 2 to 4 choices, but got {len(self.choices)}."
                )
        return self
