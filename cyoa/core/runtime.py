from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EnginePhase(StrEnum):
    IDLE = "idle"
    INITIALIZING = "initializing"
    GENERATING = "generating"
    READY = "ready"
    RESTORING = "restoring"
    ERROR = "error"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class EngineTransition:
    from_phase: EnginePhase
    to_phase: EnginePhase
    reason: str
    metadata: dict[str, Any] | None = None
