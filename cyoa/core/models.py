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


class Companion(BaseModel):
    name: str = Field(description="Display name for the companion.")
    status: Literal["available", "active", "lost"] = Field(
        default="available",
        description="Current companion roster status.",
    )
    affinity: int = Field(default=0, description="Relationship strength with this companion.")
    summary: str | None = Field(
        default=None,
        description="Short player-facing description of who this companion is.",
    )
    effect: str | None = Field(
        default=None,
        description="Short note describing the current support this companion provides.",
    )

    @model_validator(mode="after")
    def normalize_text_fields(self) -> "Companion":
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Companion name cannot be empty.")
        if self.summary is not None:
            normalized_summary = self.summary.strip()
            self.summary = normalized_summary or None
        if self.effect is not None:
            normalized_effect = self.effect.strip()
            self.effect = normalized_effect or None
        return self


class WorldTime(BaseModel):
    day: int = Field(default=1, ge=1, description="Current in-world day number.")
    hour: int = Field(
        default=8,
        ge=0,
        le=23,
        description="Current in-world hour in 24-hour time.",
    )

    def period(self) -> Literal["dawn", "morning", "afternoon", "dusk", "night"]:
        if 5 <= self.hour <= 7:
            return "dawn"
        if 8 <= self.hour <= 11:
            return "morning"
        if 12 <= self.hour <= 16:
            return "afternoon"
        if 17 <= self.hour <= 19:
            return "dusk"
        return "night"

    def summary(self) -> str:
        return f"Day {self.day}, {self.period().title()} ({self.hour:02d}:00)"

    def advance(self, hours: int) -> "WorldTime":
        if hours <= 0:
            return self.model_copy()
        absolute_hours = ((self.day - 1) * 24) + self.hour + hours
        return WorldTime(day=(absolute_hours // 24) + 1, hour=absolute_hours % 24)


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
    companions: dict[str, Literal["available", "active", "lost"]] = Field(
        default_factory=dict,
        description="Companion roster states required before this choice is available.",
    )
    companion_affinity: dict[str, int] = Field(
        default_factory=dict,
        description="Minimum companion affinity thresholds required before this choice is available.",
    )
    min_day: int | None = Field(
        default=None,
        ge=1,
        description="Minimum in-world day required before this choice is available.",
    )
    max_day: int | None = Field(
        default=None,
        ge=1,
        description="Maximum in-world day when this choice remains available.",
    )
    allowed_periods: list[Literal["dawn", "morning", "afternoon", "dusk", "night"]] = Field(
        default_factory=list,
        description="Allowed time-of-day periods for this choice.",
    )


class ChoiceCheck(BaseModel):
    stat: str = Field(description="The player stat tested by this risky choice.")
    difficulty: int = Field(
        ge=1,
        description="Target number the final roll must meet or exceed.",
    )
    stakes: str | None = Field(
        default=None,
        description="What is at risk if the player fails this check.",
    )

    @model_validator(mode="after")
    def normalize_text_fields(self) -> "ChoiceCheck":
        self.stat = self.stat.strip()
        if not self.stat:
            raise ValueError("Choice check stat cannot be empty.")
        if self.stakes is not None:
            normalized_stakes = self.stakes.strip()
            self.stakes = normalized_stakes or None
        return self


class ResolvedChoiceCheck(BaseModel):
    stat: str = Field(description="The player stat tested by the resolved choice.")
    stat_value: int = Field(description="The stat value used for the roll.")
    difficulty: int = Field(
        ge=1,
        description="The target number the player needed to meet or exceed.",
    )
    roll: int = Field(
        ge=1,
        description="The random roll applied during resolution.",
    )
    total: int = Field(description="The final total compared against the difficulty.")
    success: bool = Field(description="Whether the resolved check succeeded.")
    stakes: str | None = Field(
        default=None,
        description="What was at risk when the check was attempted.",
    )

    @model_validator(mode="after")
    def normalize_text_fields(self) -> "ResolvedChoiceCheck":
        self.stat = self.stat.strip()
        if not self.stat:
            raise ValueError("Resolved choice check stat cannot be empty.")
        if self.stakes is not None:
            normalized_stakes = self.stakes.strip()
            self.stakes = normalized_stakes or None
        return self

    def stat_label(self) -> str:
        return self.stat.replace("_", " ")

    def summary_lines(self) -> list[str]:
        result = "passed" if self.success else "failed"
        lines = [
            (
                f"Last check: {self.stat_label()} {result} "
                f"({self.roll} + {self.stat_value} = {self.total} vs {self.difficulty})"
            )
        ]
        if self.stakes:
            lines.append(f"Stakes: {self.stakes}")
        return lines


class Choice(BaseModel):
    text: str = Field(description="The description of the action the user can take.")
    requirements: ChoiceRequirement = Field(
        default_factory=ChoiceRequirement,
        description="Optional requirements gating this choice.",
    )
    check: ChoiceCheck | None = Field(
        default=None,
        description=(
            "Optional risky skill check for this choice. Use this for uncertain actions "
            "that should stay available but resolve through a roll."
        ),
    )

    def check_summary(self) -> list[str]:
        if self.check is None:
            return []
        stat_label = self.check.stat.replace("_", " ")
        lines = [f"Check: {stat_label} vs difficulty {self.check.difficulty}"]
        if self.check.stakes:
            lines.append(f"Stakes: {self.check.stakes}")
        return lines

    def availability_reason(
        self,
        inventory: list[str],
        stats: dict[str, int],
        flags: set[str],
        companions: list[Companion] | None = None,
        world_time: WorldTime | dict[str, int] | None = None,
    ) -> str | None:
        missing_requirements = self.unmet_requirements(
            inventory,
            stats,
            flags,
            companions,
            world_time,
        )
        if not missing_requirements:
            return None
        return " | ".join(missing_requirements)

    def unmet_requirements(
        self,
        inventory: list[str],
        stats: dict[str, int],
        flags: set[str],
        companions: list[Companion] | None = None,
        world_time: WorldTime | dict[str, int] | None = None,
    ) -> list[str]:
        companion_index = self._normalize_companion_index(companions)
        normalized_time = self._normalize_world_time(world_time)
        return [
            *self._missing_item_requirements(inventory),
            *self._missing_stat_requirements(stats),
            *self._missing_flag_requirements(flags),
            *self._missing_companion_requirements(companion_index),
            *self._missing_time_requirements(normalized_time),
        ]

    @staticmethod
    def _normalize_companion_index(
        companions: list[Companion] | None,
    ) -> dict[str, Companion]:
        companion_index: dict[str, Companion] = {}
        for raw_companion in companions or []:
            if isinstance(raw_companion, Companion):
                companion = raw_companion
            elif isinstance(raw_companion, dict):
                try:
                    companion = Companion(**raw_companion)
                except Exception:
                    continue
            else:
                continue
            if companion.name.strip():
                companion_index[companion.name.casefold()] = companion
        return companion_index

    @staticmethod
    def _normalize_world_time(
        world_time: WorldTime | dict[str, int] | None,
    ) -> WorldTime | None:
        if isinstance(world_time, WorldTime):
            return world_time
        if isinstance(world_time, dict):
            try:
                return WorldTime(**world_time)
            except Exception:
                return None
        return None

    def _missing_item_requirements(self, inventory: list[str]) -> list[str]:
        missing_items = [item for item in self.requirements.items if item not in inventory]
        if not missing_items:
            return []
        item_label = "item" if len(missing_items) == 1 else "items"
        return [f"Missing {item_label}: {', '.join(missing_items)}"]

    def _missing_stat_requirements(self, stats: dict[str, int]) -> list[str]:
        missing_requirements: list[str] = []
        for stat, minimum in self.requirements.stats.items():
            current = stats.get(stat, 0)
            if current < minimum:
                missing_requirements.append(
                    f"Need {stat.replace('_', ' ')} {minimum}+ (current: {current})"
                )
        return missing_requirements

    def _missing_flag_requirements(self, flags: set[str]) -> list[str]:
        missing_flags = [flag for flag in self.requirements.flags if flag not in flags]
        if not missing_flags:
            return []
        flag_label = "event" if len(missing_flags) == 1 else "events"
        return [f"Missing {flag_label}: {', '.join(missing_flags)}"]

    def _missing_companion_requirements(
        self,
        companion_index: dict[str, Companion],
    ) -> list[str]:
        missing_requirements: list[str] = []
        for companion_name, required_status in self.requirements.companions.items():
            companion = companion_index.get(companion_name.casefold())
            if companion is None:
                missing_requirements.append(f"Need {required_status} companion: {companion_name}")
            elif companion.status != required_status:
                missing_requirements.append(
                    f"Need {companion_name} to be {required_status} (current: {companion.status})"
                )

        for companion_name, minimum in self.requirements.companion_affinity.items():
            companion = companion_index.get(companion_name.casefold())
            current_affinity = companion.affinity if companion is not None else 0
            if current_affinity < minimum:
                missing_requirements.append(
                    f"Need {companion_name} affinity {minimum}+ (current: {current_affinity})"
                )
        return missing_requirements

    def _missing_time_requirements(self, world_time: WorldTime | None) -> list[str]:
        if (
            self.requirements.min_day is None
            and self.requirements.max_day is None
            and not self.requirements.allowed_periods
        ):
            return []
        if world_time is None:
            return ["Need a valid world time context"]

        missing_requirements: list[str] = []
        if self.requirements.min_day is not None and world_time.day < self.requirements.min_day:
            missing_requirements.append(
                f"Available from day {self.requirements.min_day} (current: day {world_time.day})"
            )
        if self.requirements.max_day is not None and world_time.day > self.requirements.max_day:
            missing_requirements.append(
                f"Expired after day {self.requirements.max_day} (current: day {world_time.day})"
            )
        if self.requirements.allowed_periods:
            current_period = world_time.period()
            if current_period not in self.requirements.allowed_periods:
                allowed = ", ".join(
                    period.replace("_", " ") for period in self.requirements.allowed_periods
                )
                missing_requirements.append(
                    f"Available during {allowed} (current: {current_period})"
                )
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
    companions_updated: list[Companion] = Field(
        default_factory=list,
        description=(
            "Companion roster updates for recruitable allies. "
            "Use status values available, active, or lost."
        ),
    )
    time_advance_hours: int = Field(
        default=0,
        ge=0,
        description="How many in-world hours pass before the next choice point.",
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
    companions_updated: list[Companion] = Field(
        default_factory=list,
        description="Companion roster updates derived from the narrative.",
    )
    time_advance_hours: int = Field(
        default=0,
        ge=0,
        description="How many in-world hours the narrative consumed.",
    )
