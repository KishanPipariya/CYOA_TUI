from unittest.mock import MagicMock

import pytest

from cyoa.core import observability as obs
from cyoa.db.rag_memory import NarrativeMemory, NPCMemory


def test_setup_observability_uses_real_runtime_when_extra_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.sdk.resources")

    monkeypatch.setenv("CYOA_ENABLE_OBSERVABILITY", "true")
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    set_tracer_provider = MagicMock()
    set_meter_provider = MagicMock()
    monkeypatch.setattr(obs.trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(obs.metrics, "set_meter_provider", set_meter_provider)

    obs.setup_observability()

    tracer_provider = set_tracer_provider.call_args.args[0]
    meter_provider = set_meter_provider.call_args.args[0]

    assert obs._OTEL_RUNTIME.available is True
    assert type(tracer_provider).__module__.startswith("opentelemetry.")
    assert type(meter_provider).__module__.startswith("opentelemetry.")


def test_narrative_memory_verifies_with_real_chromadb_when_extra_is_installed() -> None:
    pytest.importorskip("chromadb")

    mem = NarrativeMemory()
    try:
        assert mem.verify_availability() is True
        assert mem._collection is not None
        assert mem._client is not None
    finally:
        mem.close()


def test_npc_memory_verifies_with_real_chromadb_when_extra_is_installed() -> None:
    pytest.importorskip("chromadb")

    mem = NPCMemory()
    try:
        assert mem.verify_availability() is True
        assert mem._client is not None
        assert mem._collections
    finally:
        mem.close()
