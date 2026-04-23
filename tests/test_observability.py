from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, TypedDict, cast
from unittest.mock import MagicMock

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
    events: list[tuple[str, dict[str, object]]] = field(default_factory=list)
    exceptions: list[BaseException] = field(default_factory=list)
    status: obs.Status | None = None
    ended: bool = False

    def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
        self.events.append((name, attributes or {}))

    def record_exception(self, exc: BaseException) -> None:
        self.exceptions.append(exc)

    def set_status(self, status: obs.Status) -> None:
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


class FakeTelemetry(TypedDict):
    tracer: FakeTracer
    db_latency_histogram: FakeMetric
    db_operation_counter: FakeMetric
    db_error_counter: FakeMetric
    engine_turn_duration_histogram: FakeMetric
    engine_event_counter: FakeMetric
    ttft_histogram: FakeMetric
    tps_histogram: FakeMetric
    success_counter: FakeMetric
    failure_counter: FakeMetric
    repair_counter: FakeMetric
    fallback_counter: FakeMetric
    provider_cache_counter: FakeMetric
    startup_latency_histogram: FakeMetric


@pytest.fixture
def fake_telemetry(monkeypatch: pytest.MonkeyPatch) -> FakeTelemetry:
    metrics: FakeTelemetry = {
        "tracer": FakeTracer(),
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
        "fallback_counter": FakeMetric(),
        "provider_cache_counter": FakeMetric(),
        "startup_latency_histogram": FakeMetric(),
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
    fake_telemetry: FakeTelemetry,
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
    fake_telemetry: FakeTelemetry,
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
    assert span.status is not None
    assert span.status.status_code == obs.StatusCode.ERROR


def test_db_observed_session_warns_when_elapsed_is_checked_before_enter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = obs.DBObservedSession("neo4j", "query")

    with caplog.at_level("WARNING"):
        elapsed = session._elapsed_ms()

    assert elapsed == 0.0
    assert "DBObservedSession exited before start time was initialized" in caplog.text


def test_engine_observed_session_tracks_process_turn_success(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: FakeTelemetry,
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


def test_engine_observed_session_warns_when_elapsed_is_checked_before_enter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    session = obs.EngineObservedSession("process_turn")

    with caplog.at_level("WARNING"):
        elapsed = session._elapsed_ms()

    assert elapsed == 0.0
    assert "EngineObservedSession exited before start time was initialized" in caplog.text


def test_llm_observed_session_records_success_metrics(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: FakeTelemetry,
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
    fake_telemetry: FakeTelemetry,
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


def test_llm_observed_session_ignores_first_token_without_start(
    fake_telemetry: FakeTelemetry,
) -> None:
    session = obs.LLMObservedSession(model_name="mock-model", task="generation")

    session.report_first_token()

    assert fake_telemetry["ttft_histogram"].records == []


def test_llm_observed_session_ignores_duplicate_first_token_reports(
    monkeypatch: pytest.MonkeyPatch,
    fake_telemetry: FakeTelemetry,
) -> None:
    _patch_perf_counter(monkeypatch, [2.0, 2.2, 2.4])

    session = obs.LLMObservedSession(model_name="mock-model", task="generation").start()
    session.report_first_token()
    session.report_first_token()

    assert len(fake_telemetry["ttft_histogram"].records) == 1
    assert len(fake_telemetry["tracer"].spans[0].events) == 1
    event_name, event_attributes = fake_telemetry["tracer"].spans[0].events[0]
    assert event_name == "first_token"
    assert event_attributes["ttft_ms"] == pytest.approx(200.0)


def test_llm_observed_session_warns_when_end_called_before_start(
    caplog: pytest.LogCaptureFixture,
    fake_telemetry: FakeTelemetry,
) -> None:
    session = obs.LLMObservedSession(model_name="mock-model", task="generation")
    session.span = cast(Any, FakeSpan(name="llm.generate.generation", attributes={}))

    with caplog.at_level("WARNING"):
        session.end()

    assert "LLMObservedSession ended before start time was initialized" in caplog.text
    assert isinstance(session.span, FakeSpan)
    assert session.span.ended is True
    assert fake_telemetry["success_counter"].adds == []
    assert fake_telemetry["failure_counter"].adds == []


def test_record_repair_attempt_increments_counter(fake_telemetry: FakeTelemetry) -> None:
    obs.record_repair_attempt("mock-model", "JSONDecodeError")

    assert fake_telemetry["repair_counter"].adds == [
        (1, {"llm.model": "mock-model", "error_type": "JSONDecodeError"})
    ]


def test_extended_observability_helpers_record_metrics(fake_telemetry: FakeTelemetry) -> None:
    obs.record_fallback_node("invalid_json")
    obs.record_provider_cache_state_save(hit=True)
    obs.record_provider_cache_state_restore(hit=False)
    obs.record_startup_latency(123.0, status="success")

    assert fake_telemetry["fallback_counter"].adds == [(1, {"reason": "invalid_json"})]
    assert fake_telemetry["provider_cache_counter"].adds == [
        (1, {"operation": "save", "hit": "True"}),
        (1, {"operation": "restore", "hit": "False"}),
    ]
    assert fake_telemetry["startup_latency_histogram"].records == [
        (123.0, {"status": "success"})
    ]


def test_setup_observability_without_otlp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    set_tracer_provider = MagicMock()
    set_meter_provider = MagicMock()
    create_resource = MagicMock(return_value="resource")
    tracer_provider_factory = MagicMock(return_value=tracer_provider)
    meter_provider_factory = MagicMock(return_value=meter_provider)

    monkeypatch.setenv("CYOA_ENABLE_OBSERVABILITY", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(obs.Resource, "create", create_resource)
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory)
    monkeypatch.setattr(obs, "_is_otlp_endpoint_reachable", MagicMock(return_value=False))
    monkeypatch.setattr(obs.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(obs.metrics, "set_meter_provider", set_meter_provider)

    with caplog.at_level("INFO"):
        obs.setup_observability()

    create_resource.assert_called_once_with({"service.name": obs.SERVICE_NAME})
    tracer_provider_factory.assert_called_once_with(resource="resource")
    meter_provider_factory.assert_called_once_with(resource="resource", metric_readers=[])
    tracer_provider.add_span_processor.assert_not_called()
    set_tracer_provider.assert_called_once_with(tracer_provider)
    set_meter_provider.assert_called_once_with(meter_provider)
    assert "Observability enabled without OTLP export endpoint" in caplog.text


def test_setup_observability_returns_early_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracer_provider_factory = MagicMock()
    meter_provider_factory = MagicMock()

    monkeypatch.delenv("CYOA_ENABLE_OBSERVABILITY", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory)

    obs.setup_observability()


def test_setup_observability_returns_early_when_only_otlp_endpoint_is_set(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer_provider_factory = MagicMock()
    meter_provider_factory = MagicMock()

    monkeypatch.delenv("CYOA_ENABLE_OBSERVABILITY", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory)

    with caplog.at_level("INFO"):
        obs.setup_observability()

    tracer_provider_factory.assert_not_called()
    meter_provider_factory.assert_not_called()
    assert "observability is disabled" in caplog.text.lower()


def test_setup_observability_warns_when_optional_dependency_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer_provider_factory = MagicMock()
    meter_provider_factory = MagicMock()

    monkeypatch.setenv("CYOA_ENABLE_OBSERVABILITY", "1")
    monkeypatch.setattr(obs, "_OTEL_AVAILABLE", False)
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory, raising=False)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory, raising=False)

    with caplog.at_level("WARNING"):
        obs.setup_observability()

    assert "Install the 'observability' extra" in caplog.text

    tracer_provider_factory.assert_not_called()
    meter_provider_factory.assert_not_called()


def test_setup_observability_with_otlp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    span_exporter = MagicMock()
    metric_exporter = MagicMock()
    span_processor = MagicMock()
    metric_reader = MagicMock()
    set_tracer_provider = MagicMock()
    set_meter_provider = MagicMock()
    create_resource = MagicMock(return_value="resource")
    tracer_provider_factory = MagicMock(return_value=tracer_provider)
    meter_provider_factory = MagicMock(return_value=meter_provider)
    span_exporter_factory = MagicMock(return_value=span_exporter)
    span_processor_factory = MagicMock(return_value=span_processor)
    metric_exporter_factory = MagicMock(return_value=metric_exporter)
    metric_reader_factory = MagicMock(return_value=metric_reader)

    monkeypatch.setenv("CYOA_ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel:4318")
    monkeypatch.setattr(obs.Resource, "create", create_resource)
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory)
    monkeypatch.setattr(obs, "_is_otlp_endpoint_reachable", MagicMock(return_value=True))
    monkeypatch.setattr(obs, "OTLPSpanExporter", span_exporter_factory)
    monkeypatch.setattr(obs, "BatchSpanProcessor", span_processor_factory)
    monkeypatch.setattr(obs, "OTLPMetricExporter", metric_exporter_factory)
    monkeypatch.setattr(
        obs,
        "PeriodicExportingMetricReader",
        metric_reader_factory,
    )
    monkeypatch.setattr(obs.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(obs.metrics, "set_meter_provider", set_meter_provider)

    with caplog.at_level("INFO"):
        obs.setup_observability()

    span_exporter_factory.assert_called_once_with()
    span_processor_factory.assert_called_once_with(span_exporter)
    tracer_provider.add_span_processor.assert_called_once_with(span_processor)
    metric_exporter_factory.assert_called_once_with()
    metric_reader_factory.assert_called_once_with(metric_exporter)
    meter_provider_factory.assert_called_once_with(
        resource="resource",
        metric_readers=[metric_reader],
    )
    set_tracer_provider.assert_called_once_with(tracer_provider)
    set_meter_provider.assert_called_once_with(meter_provider)
    assert "OTLP Trace Exporter initialized." in caplog.text
    assert "OTLP Metric Exporter initialized." in caplog.text


def test_setup_observability_with_unreachable_otlp_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tracer_provider = MagicMock()
    meter_provider = MagicMock()
    set_tracer_provider = MagicMock()
    set_meter_provider = MagicMock()
    create_resource = MagicMock(return_value="resource")
    tracer_provider_factory = MagicMock(return_value=tracer_provider)
    meter_provider_factory = MagicMock(return_value=meter_provider)
    span_exporter_factory = MagicMock()
    metric_exporter_factory = MagicMock()

    monkeypatch.setenv("CYOA_ENABLE_OBSERVABILITY", "true")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    monkeypatch.setattr(obs.Resource, "create", create_resource)
    monkeypatch.setattr(obs, "TracerProvider", tracer_provider_factory)
    monkeypatch.setattr(obs, "MeterProvider", meter_provider_factory)
    monkeypatch.setattr(obs, "_is_otlp_endpoint_reachable", MagicMock(return_value=False))
    monkeypatch.setattr(obs, "OTLPSpanExporter", span_exporter_factory)
    monkeypatch.setattr(obs, "OTLPMetricExporter", metric_exporter_factory)
    monkeypatch.setattr(obs.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(obs.metrics, "set_meter_provider", set_meter_provider)

    with caplog.at_level("INFO"):
        obs.setup_observability()

    span_exporter_factory.assert_not_called()
    metric_exporter_factory.assert_not_called()
    tracer_provider.add_span_processor.assert_not_called()
    set_tracer_provider.assert_called_once_with(tracer_provider)
    set_meter_provider.assert_called_once_with(meter_provider)
    assert "OTLP endpoint configured but unavailable; tracing will stay local." in caplog.text
