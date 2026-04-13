import logging
import os
import time
from types import TracebackType
from typing import Self

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, Status, StatusCode

logger = logging.getLogger(__name__)

# Default service name
SERVICE_NAME = "cyoa-tui"


def setup_observability() -> None:
    """Initialize OpenTelemetry tracers and meters."""
    resource = Resource.create({"service.name": SERVICE_NAME})

    # Trace setup
    tracer_provider = TracerProvider(resource=resource)

    # Check for OTLP endpoint, fallback to console or no-op if you prefer
    # For now we'll use OTLP if an endpoint is set, otherwise maybe just logs?
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

    if otlp_endpoint:
        span_exporter = OTLPSpanExporter()
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        logger.info("OTLP Trace Exporter initialized.")
    else:
        logger.info("No OTEL_EXPORTER_OTLP_ENDPOINT found; tracing will be no-op or local.")

    trace.set_tracer_provider(tracer_provider)

    # Metrics setup
    metric_reader = None
    if otlp_endpoint:
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


class DBObservedSession:
    """Helper to track timing and errors for a single DB call."""

    def __init__(self, db_type: str, operation: str) -> None:
        self.db_type = db_type
        self.operation = operation
        self.start_time: float | None = None
        self.span: Span | None = None

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
                self.span.set_status(Status(StatusCode.ERROR))

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
        self.span: Span | None = None

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
            self.span.set_status(Status(StatusCode.ERROR))

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
        self.span: Span | None = None

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
