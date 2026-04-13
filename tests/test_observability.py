from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest

from cyoa.core import observability as obs


@dataclass
class FakeMetric:
    records: list[tuple[float, dict[str, str]]] = field(default_factory=list)
    adds: list[tuple[int, dict[str, str]]] = field(default_factory=list)

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.records.append((value, attributes or {}))

    def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
        self.adds.append((value, attributes or {}))


@dataclass
class FakeSpan:
    name: str
    attributes: dict[str, object]
    events: list[tuple[str, dict[str, float]]] = field(default_factory=list)
    exceptions: list[BaseException] = field(default_factory=list)
    status: object | None = None
    ended: bool = False

    def add_event(self, name: str, attributes: dict[str, float] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)

    def set_status(self, status: object) -> None:
        self.status = status

    def set_attribute(self, key: str, value: object) -> None:
        self.attributes[key] = value

    def end(self) -> None:
        self.ended = True


class FakeTracer:
    def __init__(self) -> None:
        self.spans: list[FakeSpan] = []

    def start_span(self, name: str, attributes: dict[str, object]) -> FakeSpan:
        span = FakeSpan(name=name, attributes=dict(attributes))
        self.spans.append(span)
        return span


@pytest.fixture
def fake_telemetry(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    tracer = FakeTracer()
    metrics = {
        "tracer": tracer,
        "db_latency_histogram": FakeMetric(),
        "db_operation_counter": FakeMetric(),
        "db_error_counter": FakeMetric(),
        "engine_turn_duration_histogram": FakeMetric(),
        "engine_event_counter": FakeMetric(),
        "ttft_histogram": FakeMetric(),
        "tps_histogram": FakeMetric(),
        "success_counter": FakeMetric(),
        "failure_counter": FakeMetric(),
        "repair_counter": FakeMetric(),
    }

    for name, value in metrics.items():
        monkeypatch.setattr(obs, name, value)

    return metrics


def _patch_perf_counter(
    monkeypatch: pytest.MonkeyPatch,
    values: list[float],
) -> None:
    iterator: Iterator[float] = iter(values)
    monkeypatch.setattr(obs.time, "perf_counter", lambda: next(iterator))


def test_db_observed_session_records_success_path(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: dict[str, object],
) -> None:
    _patch_perf_counter(monkeypatch, [10.0, 10.25])

    with obs.DBObservedSession("neo4j", "save_scene"):
        pass

    latency = fake_telemetry["db_latency_histogram"]
    operations = fake_telemetry["db_operation_counter"]
    tracer = fake_telemetry["tracer"]

    assert latency.records == [
        (250.0, {"db.type": "neo4j", "db.operation": "save_scene"})
    ]
    assert operations.adds == [
        (1, {"db.type": "neo4j", "db.operation": "save_scene"})
    ]
    assert tracer.spans[0].ended is True


def test_db_observed_session_records_failure_path(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: dict[str, object],
) -> None:
    _patch_perf_counter(monkeypatch, [20.0, 20.05])

    with pytest.raises(ValueError, match="db failed"):
        with obs.DBObservedSession("neo4j", "query"):
            raise ValueError("db failed")

    errors = fake_telemetry["db_error_counter"]
    tracer = fake_telemetry["tracer"]
    span = tracer.spans[0]

    assert errors.adds == [
        (
            1,
            {
                "db.type": "neo4j",
                "db.operation": "query",
                "error_type": "ValueError",
            },
        )
    ]
    assert len(span.exceptions) == 1
    assert span.ended is True
    assert span.status.status_code == obs.StatusCode.ERROR


def test_engine_observed_session_tracks_process_turn_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: dict[str, object],
) -> None:
    _patch_perf_counter(monkeypatch, [1.0, 1.15])

    with obs.EngineObservedSession("process_turn"):
        pass

    durations = fake_telemetry["engine_turn_duration_histogram"]
    events = fake_telemetry["engine_event_counter"]

    assert len(durations.records) == 1
    assert durations.records[0][0] == pytest.approx(150.0)
    assert durations.records[0][1] == {}
    assert events.adds == [
        (1, {"engine.operation": "process_turn", "success": "True"})
    ]


def test_llm_observed_session_records_success_metrics(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: dict[str, object],
) -> None:
    _patch_perf_counter(monkeypatch, [5.0, 5.1, 5.6])

    session = obs.LLMObservedSession(model_name="mock-model", task="generation").start()
    session.report_first_token()
    session.report_token(12)
    session.end(success=True)

    ttft = fake_telemetry["ttft_histogram"]
    tps = fake_telemetry["tps_histogram"]
    success = fake_telemetry["success_counter"]
    tracer = fake_telemetry["tracer"]
    span = tracer.spans[0]

    assert len(ttft.records) == 1
    assert ttft.records[0][0] == pytest.approx(100.0)
    assert ttft.records[0][1] == {"llm.model": "mock-model", "llm.task": "generation"}
    assert len(tps.records) == 1
    assert tps.records[0][0] == pytest.approx(20.0)
    assert tps.records[0][1] == {"llm.model": "mock-model", "llm.task": "generation"}
    assert success.adds == [
        (1, {"llm.model": "mock-model", "llm.task": "generation"})
    ]
    assert len(span.events) == 1
    assert span.events[0][0] == "first_token"
    assert span.events[0][1]["ttft_ms"] == pytest.approx(100.0)
    assert span.attributes["llm.tokens"] == 12
    assert span.attributes["llm.duration_s"] == pytest.approx(0.6)
    assert span.ended is True


def test_llm_observed_session_records_failure_metrics(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: dict[str, object],
) -> None:
    _patch_perf_counter(monkeypatch, [7.0, 7.5])

    session = obs.LLMObservedSession(model_name="mock-model", task="repair").start()
    session.end(success=False)

    failure = fake_telemetry["failure_counter"]
    success = fake_telemetry["success_counter"]
    tps = fake_telemetry["tps_histogram"]
    tracer = fake_telemetry["tracer"]
    span = tracer.spans[0]

    assert failure.adds == [
        (1, {"llm.model": "mock-model", "llm.task": "repair"})
    ]
    assert success.adds == []
    assert tps.records == []
    assert span.attributes["llm.tokens"] == 0
    assert span.attributes["llm.duration_s"] == pytest.approx(0.5)
    assert span.ended is True
