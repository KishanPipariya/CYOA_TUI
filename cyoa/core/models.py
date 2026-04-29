from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Objective(BaseModel):
    id: str = Field(description="Stable objective identifier.")
    text: str = Field(description="Objective text shown to the player.")
    status: str = Field(
        default="active",
        description="Objective status. Usually active, completed, or failed.",
    )


class LoreEntry(BaseModel):
    category: Literal["npc", "location", "faction", "item"] = Field(
        description="The codex bucket this entry belongs to."
    )
    name: str = Field(description="Display name for the discovered lore entry.")
    summary: str = Field(description="Short player-facing summary of what is known so far.")
    discovered_turn: int | None = Field(
        default=None,
        description="Turn when this lore entry was first discovered.",
    )

    @model_validator(mode="after")
    def normalize_text_fields(self) -> "LoreEntry":
        self.name = self.name.strip()
        self.summary = self.summary.strip()
        if not self.name:
            raise ValueError("Lore entry name cannot be empty.")
        if not self.summary:
            raise ValueError("Lore entry summary cannot be empty.")
        return self


class ChoiceRequirement(BaseModel):
    items: list[str] = Field(
        default_factory=list,
        description="Inventory items required before this choice is available.",
    )
    stats: dict[str, int] = Field(
        default_factory=dict,
        description="Minimum stat thresholds required before this choice is available.",
    )
    flags: list[str] = Field(
        default_factory=list,
        description="Story flags that must already be present before this choice is available.",
    )


class Choice(BaseModel):
    text: str = Field(description="The description of the action the user can take.")
    requirements: ChoiceRequirement = Field(
        default_factory=ChoiceRequirement,
        description="Optional requirements gating this choice.",
    )

    def availability_reason(
        self,
        inventory: list[str],
        stats: dict[str, int],
        flags: set[str],
    ) -> str | None:
        missing_requirements = self.unmet_requirements(inventory, stats, flags)
        if not missing_requirements:
            return None
        return " | ".join(missing_requirements)

    def unmet_requirements(
        self,
        inventory: list[str],
        stats: dict[str, int],
        flags: set[str],
    ) -> list[str]:
        missing_requirements: list[str] = []

        missing_items = [item for item in self.requirements.items if item not in inventory]
        if missing_items:
            item_label = "item" if len(missing_items) == 1 else "items"
            missing_requirements.append(f"Missing {item_label}: {', '.join(missing_items)}")

        for stat, minimum in self.requirements.stats.items():
            current = stats.get(stat, 0)
            if current < minimum:
                missing_requirements.append(
                    f"Need {stat.replace('_', ' ')} {minimum}+ (current: {current})"
                )

        missing_flags = [flag for flag in self.requirements.flags if flag not in flags]
        if missing_flags:
            flag_label = "event" if len(missing_flags) == 1 else "events"
            missing_requirements.append(f"Missing {flag_label}: {', '.join(missing_flags)}")

        return missing_requirements


class StoryNode(BaseModel):
    narrative: str = Field(
        description="The unfolding story text describing what just happened and the current situation."
    )
    title: str | None = Field(
        default=None,
        description="The generated title for this story adventure. (Only necessary for the very first node of the game).",
    )
    items_gained: list[str] = Field(
        default_factory=list,
        description="Items the player just picked up or earned in this turn. Only list NEW items. If none, return [].",
    )
    items_lost: list[str] = Field(
        default_factory=list,
        description="Items the player just used, dropped, or lost in this turn. If none, return [].",
    )
    npcs_present: list[str] = Field(
        default_factory=list,
        description="A list of named NPCs present in the current scene. If none, return [].",
    )
    stat_updates: dict[str, int] = Field(
        default_factory=dict,
        description="Updates to the player's stats (health, gold, reputation). E.g. {'health': -10, 'gold': 50}. Only include changes.",
    )
    choices: list[Choice] = Field(
        description="A list of 2 to 4 choices for the user's next action.",
        json_schema_extra={"minItems": 2, "maxItems": 4},
    )
    is_ending: bool = Field(
        default=False,
        description="Set to true if this narrative is a definitive ending to the story (victory, death, etc). If true, choices may be empty.",
    )
    mood: str = Field(
        default="default",
        description="The atmospheric mood of the current scene (e.g., 'mysterious', 'heroic', 'combat', 'ethereal', 'dark', 'grimy').",
    )
    objectives_updated: list[Objective] = Field(
        default_factory=list,
        description="Objective updates that should be tracked in the UI and prompt state.",
    )
    faction_updates: dict[str, int] = Field(
        default_factory=dict,
        description="Faction or reputation deltas keyed by faction name.",
    )
    npc_affinity_updates: dict[str, int] = Field(
        default_factory=dict,
        description="NPC affinity deltas keyed by NPC name.",
    )
    story_flags_set: list[str] = Field(
        default_factory=list,
        description="Story flags unlocked by this turn for future conditional choices.",
    )
    story_flags_cleared: list[str] = Field(
        default_factory=list,
        description="Story flags that should no longer be considered active.",
    )
    lore_entries_updated: list[LoreEntry] = Field(
        default_factory=list,
        description=(
            "Lore or codex entries discovered or clarified this turn. "
            "Use categories npc, location, faction, or item."
        ),
    )

    @model_validator(mode="after")
    def validate_choices_count(self) -> "StoryNode":
        if not self.is_ending:
            if not (2 <= len(self.choices) <= 4):
                raise ValueError(
                    f"Non-ending narrative must have 2 to 4 choices, but got {len(self.choices)}."
                )
        return self


class NarratorNode(BaseModel):
    """The first phase of the Judge pattern: Narrative and Choices only."""

    narrative: str = Field(
        description="The unfolding story text describing what just happened and the current situation."
    )
    title: str | None = Field(
        default=None,
        description="The generated title for this story adventure. (Only necessary for the very first turn).",
    )
    npcs_present: list[str] = Field(
        default_factory=list,
        description="A list of named NPCs present in the current scene.",
    )
    choices: list[Choice] = Field(
        description="A list of 0 to 4 choices for the user's next action.",
        json_schema_extra={"minItems": 0, "maxItems": 4},
    )
    is_ending: bool = Field(
        default=False,
        description="Set to true if this narrative is a definitive conclusion.",
    )
    mood: str = Field(
        default="default",
        description="Atmospheric keyword (mysterious, heroic, combat, etc).",
    )


class ExtractionNode(BaseModel):
    """The second phase: Extracting specific state changes from the narrative."""

    items_gained: list[str] = Field(
        default_factory=list,
        description="Specific items the narrative explicitly states the player acquired.",
    )
    items_lost: list[str] = Field(
        default_factory=list,
        description="Specific items the narrative explicitly states the player lost or used.",
    )
    stat_updates: dict[str, int] = Field(
        default_factory=dict,
        description="Health, gold, or reputation changes derived from the narrative. E.g. {'health': -5}.",
    )
    objectives_updated: list[Objective] = Field(
        default_factory=list,
        description="Objective updates derived from the narrative.",
    )
    faction_updates: dict[str, int] = Field(
        default_factory=dict,
        description="Faction or reputation changes derived from the narrative.",
    )
    npc_affinity_updates: dict[str, int] = Field(
        default_factory=dict,
        description="NPC affinity changes derived from the narrative.",
    )
    story_flags_set: list[str] = Field(
        default_factory=list,
        description="Story flags unlocked by the narrative.",
    )
    story_flags_cleared: list[str] = Field(
        default_factory=list,
        description="Story flags retired by the narrative.",
    )
    lore_entries_updated: list[LoreEntry] = Field(
        default_factory=list,
        description=(
            "Lore or codex entries discovered or clarified by the narrative. "
            "Use categories npc, location, faction, or item."
        ),
    )
