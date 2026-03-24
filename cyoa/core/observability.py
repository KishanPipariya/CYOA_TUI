import logging
import os
import time

from opentelemetry import metrics, trace
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

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


class LLMObservedSession:
    """Helper to track timing and token counts for a single LLM call."""

    def __init__(self, model_name: str, task: str):
        self.model_name = model_name
        self.task = task
        self.start_time = None
        self.first_token_time = None
        self.token_count = 0
        self.span = None

    def start(self):
        self.start_time = time.perf_counter()
        self.span = tracer.start_span(
            f"llm.generate.{self.task}",
            attributes={"llm.model": self.model_name, "llm.task": self.task},
        )
        return self

    def report_first_token(self):
        if self.first_token_time is None:
            self.first_token_time = time.perf_counter()
            ttft_ms = (self.first_token_time - self.start_time) * 1000
            ttft_histogram.record(ttft_ms, {"llm.model": self.model_name, "llm.task": self.task})
            if self.span:
                self.span.add_event("first_token", attributes={"ttft_ms": ttft_ms})

    def report_token(self, count: int = 1):
        self.token_count += count

    def end(self, success: bool = True):
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


def record_repair_attempt(model_name: str, error_type: str):
    repair_counter.add(1, {"llm.model": model_name, "error_type": error_type})
