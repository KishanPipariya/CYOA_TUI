import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from unittest.mock import AsyncMock

import pytest

from cyoa.core import observability as obs
from cyoa.core.engine import StoryEngine
from cyoa.llm.broker import ModelBroker
from cyoa.llm.providers import LLMProvider
from cyoa.ui.app import CYOAApp


@pytest.mark.asyncio
async def test_stream_narrative_fills_queue():
    """Verify that _stream_narrative populates the typewriter queue correctly."""
    app = CYOAApp(model_path="dummy.gguf")
    app._loading_suffix_shown = False

    # Simulate streaming 10 tokens one by one
    for i in range(10):
        app._stream_narrative(f"char-{i}")

    assert app._typewriter_queue.qsize() == 10
    assert app._typewriter_queue.get_nowait() == "char-0"


@dataclass
class _FakeMetric:
    records: list[tuple[float, dict[str, str]]] = field(default_factory=list)
    adds: list[tuple[int, dict[str, str]]] = field(default_factory=list)

    def record(self, value: float, attributes: dict[str, str] | None = None) -> None:
        self.records.append((value, attributes or {}))

    def add(self, value: int, attributes: dict[str, str] | None = None) -> None:
        self.adds.append((value, attributes or {}))


class _StreamingProvider(LLMProvider):
    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)

    async def generate_text(
        self,
        messages: list[dict[str, str]],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        raise AssertionError("generate_text should not be used in this test")

    async def generate_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ) -> str:
        raise AssertionError("streaming path should be used on the first attempt")

    async def stream_json(
        self,
        messages: list[dict[str, str]],
        schema: dict[str, object],
        max_tokens: int = 512,
        temperature: float = 0.7,
    ):
        session = obs.LLMObservedSession(model_name="stream-test", task="generation").start()
        chunks = [
            '{"narrative":"A torch sputters',
            ' in the crypt.","choices":[{"text":"Advance"},{"text":"Retreat"}]}',
        ]
        for chunk in chunks:
            await asyncio.sleep(0)
            session.report_first_token()
            session.report_token(self.count_tokens(chunk))
            yield chunk
        session.end(success=True)


@pytest.mark.asyncio
async def test_process_turn_records_ttft_and_duration_guardrails(monkeypatch):
    perf_values: Iterator[float] = iter([1.0, 1.03, 1.12, 1.2, 1.2, 1.2])

    def fake_perf_counter() -> float:
        try:
            return next(perf_values)
        except StopIteration:
            return 1.2

    monkeypatch.setattr(obs.time, "perf_counter", fake_perf_counter)

    engine_turn_duration_histogram = _FakeMetric()
    engine_event_counter = _FakeMetric()
    ttft_histogram = _FakeMetric()
    tps_histogram = _FakeMetric()
    success_counter = _FakeMetric()
    failure_counter = _FakeMetric()

    monkeypatch.setattr(obs, "engine_turn_duration_histogram", engine_turn_duration_histogram)
    monkeypatch.setattr(obs, "engine_event_counter", engine_event_counter)
    monkeypatch.setattr(obs, "ttft_histogram", ttft_histogram)
    monkeypatch.setattr(obs, "tps_histogram", tps_histogram)
    monkeypatch.setattr(obs, "success_counter", success_counter)
    monkeypatch.setattr(obs, "failure_counter", failure_counter)

    broker = ModelBroker(provider=_StreamingProvider())
    engine = StoryEngine(broker=broker, starting_prompt="Start in the crypt.")
    engine.rag.index_node = AsyncMock()

    await engine.initialize()

    assert len(ttft_histogram.records) == 1
    assert ttft_histogram.records[0][1] == {
        "llm.model": "stream-test",
        "llm.task": "generation",
    }
    assert ttft_histogram.records[0][0] == pytest.approx(80.0)
    assert ttft_histogram.records[0][0] < 100.0
    assert len(engine_turn_duration_histogram.records) == 1
    assert engine_turn_duration_histogram.records[0][0] == pytest.approx(170.0)
    assert engine_turn_duration_histogram.records[0][0] < 250.0
    assert engine_turn_duration_histogram.records[0][0] >= ttft_histogram.records[0][0]
    assert (1, {"engine.operation": "process_turn", "success": "True"}) in engine_event_counter.adds
    assert len(tps_histogram.records) == 1
    assert success_counter.adds == [
        (1, {"llm.model": "stream-test", "llm.task": "generation"})
    ]
    assert failure_counter.adds == []
    assert engine.state.current_node is not None
    assert engine.state.current_node.narrative == "A torch sputters in the crypt."
