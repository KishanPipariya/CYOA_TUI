from collections.abc import Sequence
from typing import Any, Literal

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


class CampaignMilestone(BaseModel):
    id: str = Field(description="Stable milestone identifier within a campaign chapter.")
    title: str = Field(description="Player-facing milestone title.")
    summary: str | None = Field(
        default=None,
        description="Optional note describing what the milestone represents.",
    )
    required_story_flags: list[str] = Field(
        default_factory=list,
        description="Story flags that must all be active before the milestone is complete.",
    )
    required_objective_ids: list[str] = Field(
        default_factory=list,
        description="Objective identifiers that must be completed before the milestone is met.",
    )
    min_turn: int | None = Field(
        default=None,
        ge=1,
        description="Optional minimum turn before the milestone may complete.",
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignMilestone":
        self.id = self.id.strip()
        self.title = self.title.strip()
        if not self.id:
            raise ValueError("Campaign milestone id cannot be empty.")
        if not self.title:
            raise ValueError("Campaign milestone title cannot be empty.")
        if self.summary is not None:
            normalized_summary = self.summary.strip()
            self.summary = normalized_summary or None
        self.required_story_flags = [
            flag.strip() for flag in self.required_story_flags if flag.strip()
        ]
        self.required_objective_ids = [
            objective_id.strip()
            for objective_id in self.required_objective_ids
            if objective_id.strip()
        ]
        if (
            not self.required_story_flags
            and not self.required_objective_ids
            and self.min_turn is None
        ):
            raise ValueError("Campaign milestones require at least one completion condition.")
        return self

    def is_complete(
        self,
        *,
        story_flags: set[str],
        objectives: Sequence[Objective],
        turn_count: int,
    ) -> bool:
        if self.min_turn is not None and turn_count < self.min_turn:
            return False
        if self.required_story_flags and not set(self.required_story_flags).issubset(story_flags):
            return False
        if self.required_objective_ids:
            completed_objectives = {
                objective.id for objective in objectives if objective.status == "completed"
            }
            if not set(self.required_objective_ids).issubset(completed_objectives):
                return False
        return True


class CampaignClockDefinition(BaseModel):
    id: str = Field(description="Stable campaign pressure clock identifier.")
    label: str = Field(description="Terse UI label for this clock.")
    description: str | None = Field(
        default=None,
        description="Optional authoring note describing what the clock tracks.",
    )
    initial: int = Field(default=0, ge=0, description="Starting clock value.")
    minimum: int = Field(default=0, ge=0, description="Lowest allowed clock value.")
    maximum: int = Field(default=6, ge=1, description="Highest allowed clock value.")

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignClockDefinition":
        self.id = self.id.strip()
        self.label = self.label.strip()
        if not self.id:
            raise ValueError("Campaign clock id cannot be empty.")
        if not self.label:
            raise ValueError("Campaign clock label cannot be empty.")
        if self.description is not None:
            normalized_description = self.description.strip()
            self.description = normalized_description or None
        if self.minimum > self.maximum:
            raise ValueError("Campaign clock minimum cannot exceed maximum.")
        if not self.minimum <= self.initial <= self.maximum:
            raise ValueError("Campaign clock initial value must be within bounds.")
        return self


class CampaignClock(BaseModel):
    id: str = Field(description="Stable campaign pressure clock identifier.")
    label: str = Field(description="Terse UI label for this clock.")
    value: int = Field(default=0, ge=0, description="Current clock value.")
    minimum: int = Field(default=0, ge=0, description="Lowest allowed clock value.")
    maximum: int = Field(default=6, ge=1, description="Highest allowed clock value.")

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignClock":
        self.id = self.id.strip()
        self.label = self.label.strip()
        if not self.id:
            raise ValueError("Campaign clock id cannot be empty.")
        if not self.label:
            raise ValueError("Campaign clock label cannot be empty.")
        if self.minimum > self.maximum:
            raise ValueError("Campaign clock minimum cannot exceed maximum.")
        self.value = max(self.minimum, min(self.value, self.maximum))
        return self

    @classmethod
    def from_definition(cls, definition: CampaignClockDefinition) -> "CampaignClock":
        return cls(
            id=definition.id,
            label=definition.label,
            value=definition.initial,
            minimum=definition.minimum,
            maximum=definition.maximum,
        )

    def apply_delta(self, delta: int) -> bool:
        next_value = max(self.minimum, min(self.value + delta, self.maximum))
        if next_value == self.value:
            return False
        self.value = next_value
        return True

    def terse_summary(self) -> str:
        return f"{self.label} {self.value}/{self.maximum}"


class CampaignChapter(BaseModel):
    id: str = Field(description="Stable chapter identifier.")
    title: str = Field(description="Player-facing chapter title.")
    summary: str | None = Field(
        default=None,
        description="Optional chapter summary for campaign browsers or HUD surfaces.",
    )
    directives: list[str] = Field(
        default_factory=list,
        description="Prompt directives that apply while this chapter is active.",
    )
    milestones: list[CampaignMilestone] = Field(
        default_factory=list,
        description="Ordered milestones that define chapter progress.",
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignChapter":
        self.id = self.id.strip()
        self.title = self.title.strip()
        if not self.id:
            raise ValueError("Campaign chapter id cannot be empty.")
        if not self.title:
            raise ValueError("Campaign chapter title cannot be empty.")
        if self.summary is not None:
            normalized_summary = self.summary.strip()
            self.summary = normalized_summary or None
        self.directives = [directive.strip() for directive in self.directives if directive.strip()]
        milestone_ids: set[str] = set()
        for milestone in self.milestones:
            if milestone.id in milestone_ids:
                raise ValueError(f"Duplicate campaign milestone id '{milestone.id}'.")
            milestone_ids.add(milestone.id)
        return self


class CampaignAct(BaseModel):
    id: str = Field(description="Stable act identifier.")
    title: str = Field(description="Player-facing act title.")
    summary: str | None = Field(
        default=None,
        description="Optional act summary for campaign browsers or tooltips.",
    )
    chapters: list[CampaignChapter] = Field(
        description="Ordered chapters within this act.",
        json_schema_extra={"minItems": 1},
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignAct":
        self.id = self.id.strip()
        self.title = self.title.strip()
        if not self.id:
            raise ValueError("Campaign act id cannot be empty.")
        if not self.title:
            raise ValueError("Campaign act title cannot be empty.")
        if self.summary is not None:
            normalized_summary = self.summary.strip()
            self.summary = normalized_summary or None
        if not self.chapters:
            raise ValueError("Campaign acts require at least one chapter.")
        chapter_ids: set[str] = set()
        for chapter in self.chapters:
            if chapter.id in chapter_ids:
                raise ValueError(
                    f"Duplicate campaign chapter id '{chapter.id}' inside act '{self.id}'."
                )
            chapter_ids.add(chapter.id)
        return self


class CampaignPack(BaseModel):
    id: str = Field(description="Stable campaign identifier.")
    name: str = Field(description="Player-facing campaign name.")
    description: str = Field(description="Short campaign description.")
    acts: list[CampaignAct] = Field(
        description="Ordered campaign acts.",
        json_schema_extra={"minItems": 1},
    )
    starting_act_id: str | None = Field(
        default=None,
        description="Optional explicit starting act identifier.",
    )
    starting_chapter_id: str | None = Field(
        default=None,
        description="Optional explicit starting chapter identifier.",
    )
    clocks: list[CampaignClockDefinition] = Field(
        default_factory=list,
        description="Optional pressure clocks used by the adventure director.",
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignPack":
        self.id = self.id.strip()
        self.name = self.name.strip()
        self.description = self.description.strip()
        if not self.id:
            raise ValueError("Campaign id cannot be empty.")
        if not self.name:
            raise ValueError("Campaign name cannot be empty.")
        if not self.description:
            raise ValueError("Campaign description cannot be empty.")
        if not self.acts:
            raise ValueError("Campaigns require at least one act.")
        if self.starting_act_id is not None:
            self.starting_act_id = self.starting_act_id.strip() or None
        if self.starting_chapter_id is not None:
            self.starting_chapter_id = self.starting_chapter_id.strip() or None

        act_ids, chapter_to_act = self._campaign_indexes()
        self._validate_starting_position(act_ids, chapter_to_act)
        clock_ids: set[str] = set()
        for clock in self.clocks:
            if clock.id in clock_ids:
                raise ValueError(f"Duplicate campaign clock id '{clock.id}'.")
            clock_ids.add(clock.id)
        return self

    def _campaign_indexes(self) -> tuple[set[str], dict[str, str]]:
        act_ids: set[str] = set()
        chapter_to_act: dict[str, str] = {}
        for act in self.acts:
            if act.id in act_ids:
                raise ValueError(f"Duplicate campaign act id '{act.id}'.")
            act_ids.add(act.id)
            for chapter in act.chapters:
                owner = chapter_to_act.get(chapter.id)
                if owner is not None:
                    raise ValueError(
                        f"Duplicate campaign chapter id '{chapter.id}' across acts '{owner}' and '{act.id}'."
                    )
                chapter_to_act[chapter.id] = act.id
        return act_ids, chapter_to_act

    def _validate_starting_position(
        self,
        act_ids: set[str],
        chapter_to_act: dict[str, str],
    ) -> None:
        if self.starting_act_id is not None and self.starting_act_id not in act_ids:
            raise ValueError(f"Unknown starting_act_id '{self.starting_act_id}'.")
        if self.starting_chapter_id is None:
            return
        owning_act_id = chapter_to_act.get(self.starting_chapter_id)
        if owning_act_id is None:
            raise ValueError(f"Unknown starting_chapter_id '{self.starting_chapter_id}'.")
        if self.starting_act_id is not None and owning_act_id != self.starting_act_id:
            raise ValueError("starting_act_id and starting_chapter_id must point to the same act.")

    def starting_position(self) -> tuple[str, str]:
        if self.starting_chapter_id is not None:
            chapter_ref = self.get_chapter_ref(self.starting_chapter_id)
            if chapter_ref is not None:
                return chapter_ref

        first_act = self.acts[0]
        if self.starting_act_id is not None:
            resolved_act = self.get_act(self.starting_act_id)
            if resolved_act is not None:
                first_act = resolved_act
        return first_act.id, first_act.chapters[0].id

    def get_act(self, act_id: str | None) -> CampaignAct | None:
        if act_id is None:
            return None
        for act in self.acts:
            if act.id == act_id:
                return act
        return None

    def get_chapter(self, chapter_id: str | None) -> CampaignChapter | None:
        if chapter_id is None:
            return None
        for act in self.acts:
            for chapter in act.chapters:
                if chapter.id == chapter_id:
                    return chapter
        return None

    def get_chapter_ref(self, chapter_id: str | None) -> tuple[str, str] | None:
        if chapter_id is None:
            return None
        for act in self.acts:
            for chapter in act.chapters:
                if chapter.id == chapter_id:
                    return act.id, chapter.id
        return None

    def next_chapter_ref(self, chapter_id: str | None) -> tuple[str, str] | None:
        ordered_refs = [(act.id, chapter.id) for act in self.acts for chapter in act.chapters]
        for index, ref in enumerate(ordered_refs):
            if ref[1] == chapter_id:
                return ordered_refs[index + 1] if index + 1 < len(ordered_refs) else None
        return None


class CampaignChapterProgress(BaseModel):
    chapter_id: str = Field(description="Stable chapter identifier.")
    completed_milestone_ids: list[str] = Field(
        default_factory=list,
        description="Milestones already completed within this chapter.",
    )
    started_turn: int | None = Field(
        default=None,
        ge=1,
        description="Turn when the chapter first became active.",
    )
    completed_turn: int | None = Field(
        default=None,
        ge=1,
        description="Turn when the chapter was completed.",
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignChapterProgress":
        self.chapter_id = self.chapter_id.strip()
        if not self.chapter_id:
            raise ValueError("Campaign chapter progress requires a chapter id.")
        self.completed_milestone_ids = [
            milestone_id.strip()
            for milestone_id in self.completed_milestone_ids
            if milestone_id.strip()
        ]
        return self


class CampaignProgress(BaseModel):
    campaign_id: str = Field(description="Stable campaign identifier.")
    active_act_id: str = Field(description="Currently active act identifier.")
    active_chapter_id: str = Field(description="Currently active chapter identifier.")
    chapters: list[CampaignChapterProgress] = Field(
        default_factory=list,
        description="Per-chapter progress history.",
    )
    started_turn: int = Field(
        default=1,
        ge=1,
        description="Turn when the campaign run began.",
    )
    completed_turn: int | None = Field(
        default=None,
        ge=1,
        description="Turn when the campaign finished, if it has.",
    )
    clocks: list[CampaignClock] = Field(
        default_factory=list,
        description="Current campaign pressure-clock values.",
    )

    @model_validator(mode="after")
    def normalize_fields(self) -> "CampaignProgress":
        self.campaign_id = self.campaign_id.strip()
        self.active_act_id = self.active_act_id.strip()
        self.active_chapter_id = self.active_chapter_id.strip()
        if not self.campaign_id:
            raise ValueError("Campaign progress requires a campaign id.")
        if not self.active_act_id:
            raise ValueError("Campaign progress requires an active act id.")
        if not self.active_chapter_id:
            raise ValueError("Campaign progress requires an active chapter id.")
        chapter_ids: set[str] = set()
        for chapter in self.chapters:
            if chapter.chapter_id in chapter_ids:
                raise ValueError(
                    f"Duplicate campaign progress entry for chapter '{chapter.chapter_id}'."
                )
            chapter_ids.add(chapter.chapter_id)
        clock_ids: set[str] = set()
        for clock in self.clocks:
            if clock.id in clock_ids:
                raise ValueError(f"Duplicate campaign progress clock '{clock.id}'.")
            clock_ids.add(clock.id)
        return self

    @classmethod
    def from_campaign(cls, campaign: CampaignPack, *, started_turn: int = 1) -> "CampaignProgress":
        act_id, chapter_id = campaign.starting_position()
        return cls(
            campaign_id=campaign.id,
            active_act_id=act_id,
            active_chapter_id=chapter_id,
            chapters=[CampaignChapterProgress(chapter_id=chapter_id, started_turn=started_turn)],
            clocks=[CampaignClock.from_definition(clock) for clock in campaign.clocks],
            started_turn=started_turn,
        )

    def chapter_progress_for(self, chapter_id: str) -> CampaignChapterProgress | None:
        for chapter in self.chapters:
            if chapter.chapter_id == chapter_id:
                return chapter
        return None

    def ensure_chapter_progress(
        self,
        chapter_id: str,
        *,
        started_turn: int,
    ) -> CampaignChapterProgress:
        existing = self.chapter_progress_for(chapter_id)
        if existing is not None:
            if existing.started_turn is None:
                existing.started_turn = started_turn
            return existing
        created = CampaignChapterProgress(chapter_id=chapter_id, started_turn=started_turn)
        self.chapters.append(created)
        return created

    def clock_for(self, clock_id: str) -> CampaignClock | None:
        for clock in self.clocks:
            if clock.id == clock_id:
                return clock
        return None

    def sync_clock_definitions(self, campaign: CampaignPack) -> bool:
        changed = False
        known = {clock.id for clock in self.clocks}
        definitions_by_id = {clock.id: clock for clock in campaign.clocks}
        for definition in campaign.clocks:
            if definition.id not in known:
                self.clocks.append(CampaignClock.from_definition(definition))
                changed = True
        kept_clocks = [clock for clock in self.clocks if clock.id in definitions_by_id]
        if len(kept_clocks) != len(self.clocks):
            self.clocks = kept_clocks
            changed = True
        return changed

    def apply_clock_updates(self, updates: dict[str, int]) -> bool:
        changed = False
        for clock_id, delta in updates.items():
            clock = self.clock_for(clock_id)
            if clock is not None and clock.apply_delta(delta):
                changed = True
        return changed


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
        companions: Sequence[Companion | dict[str, Any]] | None = None,
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
        companions: Sequence[Companion | dict[str, Any]] | None,
    ) -> dict[str, Companion]:
        companion_index: dict[str, Companion] = {}
        for raw_companion in companions or []:
            if isinstance(raw_companion, Companion):
                companion = raw_companion
            else:
                try:
                    companion = Companion(**raw_companion)
                except Exception:
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
    campaign_clock_updates: dict[str, int] = Field(
        default_factory=dict,
        description=(
            "Deltas for active campaign clocks such as tension, danger, suspicion, "
            "corruption, or pursuit. Only use ids defined by the active campaign."
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
    companions_updated: list[Companion] = Field(
        default_factory=list,
        description="Companion roster updates derived from the narrative.",
    )
    time_advance_hours: int = Field(
        default=0,
        ge=0,
        description="How many in-world hours the narrative consumed.",
    )
    campaign_clock_updates: dict[str, int] = Field(
        default_factory=dict,
        description="Pressure-clock deltas derived from the narrative.",
    )
