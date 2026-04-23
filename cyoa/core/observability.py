import logging
import os
import socket
import time
from enum import Enum
from types import TracebackType
from typing import Any, Self
from urllib.parse import urlparse

otel_metrics: Any
otel_trace: Any
OTelSpan: Any
OTelStatus: Any
OTelStatusCode: Any

try:
    from opentelemetry import metrics as otel_metrics
    from opentelemetry import trace as otel_trace
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.trace import Span as OTelSpan
    from opentelemetry.trace import Status as OTelStatus
    from opentelemetry.trace import StatusCode as OTelStatusCode

    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via fallback behavior
    _OTEL_AVAILABLE = False

    class _FallbackStatusCode(Enum):
        ERROR = "ERROR"

    class _FallbackStatus:
        def __init__(self, status_code: _FallbackStatusCode):
            self.status_code = status_code

    class _FallbackSpan:
        def add_event(self, name: str, attributes: dict[str, object] | None = None) -> None:
            del name, attributes

        def record_exception(self, exc: BaseException) -> None:
            del exc

        def set_status(self, status: "_FallbackStatus") -> None:
            del status

        def set_attribute(self, key: str, value: object) -> None:
            del key, value

        def end(self) -> None:
            return None

    class _NoopMetric:
        def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
            del value, attributes

        def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
            del value, attributes

    class _NoopMeter:
        def create_histogram(self, name: str, description: str, unit: str) -> _NoopMetric:
            del name, description, unit
            return _NoopMetric()

        def create_counter(self, name: str, description: str) -> _NoopMetric:
            del name, description
            return _NoopMetric()

    class _NoopTracer:
        def start_span(self, name: str, attributes: dict[str, object]) -> "_FallbackSpan":
            del name, attributes
            return _FallbackSpan()

    class _NoopMetricsAPI:
        @staticmethod
        def get_meter(name: str) -> _NoopMeter:
            del name
            return _NoopMeter()

        @staticmethod
        def set_meter_provider(provider: object) -> None:
            del provider

    class _NoopTraceAPI:
        @staticmethod
        def get_tracer(name: str) -> _NoopTracer:
            del name
            return _NoopTracer()

        @staticmethod
        def set_tracer_provider(provider: object) -> None:
            del provider

    otel_metrics = _NoopMetricsAPI()
    otel_trace = _NoopTraceAPI()
    OTelSpan = _FallbackSpan
    OTelStatus = _FallbackStatus
    OTelStatusCode = _FallbackStatusCode

metrics: Any = otel_metrics
trace: Any = otel_trace
Span = OTelSpan
Status = OTelStatus
StatusCode = OTelStatusCode

logger = logging.getLogger(__name__)

# Default service name
SERVICE_NAME = "cyoa-tui"


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _is_otlp_endpoint_reachable(endpoint: str) -> bool:
    """Return whether the configured OTLP collector appears reachable."""
    parsed = urlparse(endpoint)
    if not parsed.scheme or not parsed.hostname:
        logger.warning(
            "Ignoring invalid OTLP endpoint %r; tracing and metrics will stay local.",
            endpoint,
        )
        return False

    default_port = 4318 if parsed.scheme in {"http", "https"} else None
    port = parsed.port or default_port
    if port is None:
        logger.warning(
            "OTLP endpoint %r is missing a port; tracing and metrics will stay local.",
            endpoint,
        )
        return False

    try:
        with socket.create_connection((parsed.hostname, port), timeout=0.2):
            return True
    except OSError as exc:
        logger.warning(
            "OTLP endpoint %s:%s is unreachable (%s); tracing and metrics will stay local.",
            parsed.hostname,
            port,
            exc,
        )
        return False


def setup_observability() -> None:
    """Initialize OpenTelemetry tracers and meters."""
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    observability_enabled = _env_flag_enabled("CYOA_ENABLE_OBSERVABILITY")
    if not observability_enabled:
        if otlp_endpoint:
            logger.info(
                "OTLP endpoint configured but observability is disabled; set CYOA_ENABLE_OBSERVABILITY=true to export telemetry."
            )
        else:
            logger.debug("Observability integrations disabled for default startup.")
        return
    if not _OTEL_AVAILABLE:
        logger.warning(
            "Observability was requested, but OpenTelemetry dependencies are not installed. "
            "Install the 'observability' extra to enable OTEL export."
        )
        return

    resource = Resource.create({"service.name": SERVICE_NAME})

    # Trace setup
    tracer_provider = TracerProvider(resource=resource)

    # Check for OTLP endpoint, fallback to console or no-op if you prefer
    # For now we'll use OTLP if an endpoint is set, otherwise maybe just logs?
    otlp_available = bool(otlp_endpoint and _is_otlp_endpoint_reachable(otlp_endpoint))

    if otlp_available:
        span_exporter = OTLPSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        logger.info("OTLP Trace Exporter initialized.")
    elif otlp_endpoint:
        logger.info("OTLP endpoint configured but unavailable; tracing will stay local.")
    else:
        logger.info("Observability enabled without OTLP export endpoint; tracing will stay local.")

    trace.set_tracer_provider(tracer_provider)

    # Metrics setup
    metric_reader = None
    if otlp_available:
        metric_exporter = OTLPMetricExporter()
        metric_reader = PeriodicExportingMetricReader(metric_exporter)
        logger.info("OTLP Metric Exporter initialized.")

    meter_provider = MeterProvider(
        resource=resource, metric_readers=[metric_reader] if metric_reader else []
    )
    metrics.set_meter_provider(meter_provider)


# Initialize global tracer and meter
tracer = trace.get_tracer(__name__)
meter = metrics.get_meter(__name__)

# LLM Metrics
ttft_histogram = meter.create_histogram(
    name="llm.ttft",
    description="Time to First Token",
    unit="ms",
)

tps_histogram = meter.create_histogram(
    name="llm.tps",
    description="Tokens per Second",
    unit="tps",
)

repair_counter = meter.create_counter(
    name="llm.json_repair",
    description="Number of JSON repair attempts",
)

success_counter = meter.create_counter(
    name="llm.generation_success",
    description="Successful LLM generations",
)

failure_counter = meter.create_counter(
    name="llm.generation_failure",
    description="Failed LLM generations",
)

fallback_counter = meter.create_counter(
    name="llm.fallback",
    description="Fallback nodes emitted after provider or parsing failures",
)

provider_cache_counter = meter.create_counter(
    name="llm.provider_cache",
    description="Provider state save/load activity and hit rate",
)

# DB Metrics
db_latency_histogram = meter.create_histogram(
    name="db.operation_latency",
    description="Database operation latency",
    unit="ms",
)

db_operation_counter = meter.create_counter(
    name="db.operations",
    description="Number of database operations",
)

db_error_counter = meter.create_counter(
    name="db.errors",
    description="Number of database errors",
)

# Engine Metrics
engine_turn_duration_histogram = meter.create_histogram(
    name="engine.turn_duration",
    description="Time taken to process a single turn",
    unit="ms",
)

engine_event_counter = meter.create_counter(
    name="engine.events",
    description="Significant engine events",
)

startup_latency_histogram = meter.create_histogram(
    name="app.startup_latency",
    description="Time taken to initialize the app runtime",
    unit="ms",
)


class DBObservedSession:
    """Helper to track timing and errors for a single DB call."""

    def __init__(self, db_type: str, operation: str) -> None:
        self.db_type = db_type
        self.operation = operation
        self.start_time: float | None = None
        self.span: OTelSpan | None = None

    def __enter__(self) -> Self:
        self.start_time = time.perf_counter()
        # Use provided operation name directly for span
        self.span = tracer.start_span(
            f"db.{self.db_type}.{self.operation}",
            attributes={"db.type": self.db_type, "db.operation": self.operation},
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_tb
        duration_ms = self._elapsed_ms()
        db_latency_histogram.record(
            duration_ms, {"db.type": self.db_type, "db.operation": self.operation}
        )
        db_operation_counter.add(
            1, {"db.type": self.db_type, "db.operation": self.operation}
        )

        if exc_type:
            db_error_counter.add(
                1,
                {
                    "db.type": self.db_type,
                    "db.operation": self.operation,
                    "error_type": exc_type.__name__,
                },
            )
            if self.span and exc_val is not None:
                self.span.record_exception(exc_val)
                self.span.set_status(OTelStatus(OTelStatusCode.ERROR))

        if self.span:
            self.span.end()

    def _elapsed_ms(self) -> float:
        if self.start_time is None:
            logger.warning(
                "DBObservedSession exited before start time was initialized for %s.%s",
                self.db_type,
                self.operation,
            )
            return 0.0
        return (time.perf_counter() - self.start_time) * 1000


class EngineObservedSession:
    """Helper to track timing for engine operations."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self.start_time: float | None = None
        self.span: OTelSpan | None = None

    def __enter__(self) -> Self:
        self.start_time = time.perf_counter()
        self.span = tracer.start_span(
            f"engine.{self.operation}",
            attributes={"engine.operation": self.operation},
        )
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_tb
        duration_ms = self._elapsed_ms()
        if self.operation == "process_turn":
            engine_turn_duration_histogram.record(duration_ms)

        engine_event_counter.add(
            1, {"engine.operation": self.operation, "success": str(exc_type is None)}
        )

        if exc_type and self.span and exc_val is not None:
            self.span.record_exception(exc_val)
            self.span.set_status(OTelStatus(OTelStatusCode.ERROR))

        if self.span:
            self.span.end()

    def _elapsed_ms(self) -> float:
        if self.start_time is None:
            logger.warning(
                "EngineObservedSession exited before start time was initialized for %s",
                self.operation,
            )
            return 0.0
        return (time.perf_counter() - self.start_time) * 1000


class LLMObservedSession:
    """Helper to track timing and token counts for a single LLM call."""

    def __init__(self, model_name: str, task: str) -> None:
        self.model_name = model_name
        self.task = task
        self.start_time: float | None = None
        self.first_token_time: float | None = None
        self.token_count = 0
        self.span: OTelSpan | None = None

    def start(self) -> Self:
        self.start_time = time.perf_counter()
        self.span = tracer.start_span(
            f"llm.generate.{self.task}",
            attributes={"llm.model": self.model_name, "llm.task": self.task},
        )
        return self

    def report_first_token(self) -> None:
        if self.start_time is None or self.first_token_time is not None:
            return

        self.first_token_time = time.perf_counter()
        ttft_ms = (self.first_token_time - self.start_time) * 1000
        ttft_histogram.record(ttft_ms, {"llm.model": self.model_name, "llm.task": self.task})
        if self.span:
            self.span.add_event("first_token", attributes={"ttft_ms": ttft_ms})

    def report_token(self, count: int = 1) -> None:
        self.token_count += count

    def end(self, success: bool = True) -> None:
        if self.start_time is None:
            logger.warning(
                "LLMObservedSession ended before start time was initialized for %s/%s",
                self.model_name,
                self.task,
            )
            if self.span:
                self.span.end()
            return

        end_time = time.perf_counter()
        duration = end_time - self.start_time

        if success:
            success_counter.add(1, {"llm.model": self.model_name, "llm.task": self.task})
            if self.token_count > 0:
                tps = self.token_count / duration
                tps_histogram.record(tps, {"llm.model": self.model_name, "llm.task": self.task})
        else:
            failure_counter.add(1, {"llm.model": self.model_name, "llm.task": self.task})

        if self.span:
            self.span.set_attribute("llm.tokens", self.token_count)
            self.span.set_attribute("llm.duration_s", duration)
            self.span.end()


def record_repair_attempt(model_name: str, error_type: str) -> None:
    repair_counter.add(1, {"llm.model": model_name, "error_type": error_type})


def record_fallback_node(reason: str) -> None:
    fallback_counter.add(1, {"reason": reason})


def record_provider_cache_state_save(*, hit: bool) -> None:
    provider_cache_counter.add(1, {"operation": "save", "hit": str(hit)})


def record_provider_cache_state_restore(*, hit: bool) -> None:
    provider_cache_counter.add(1, {"operation": "restore", "hit": str(hit)})


def record_startup_latency(duration_ms: float, *, status: str) -> None:
    startup_latency_histogram.record(duration_ms, {"status": status})
