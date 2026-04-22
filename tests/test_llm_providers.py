import asyncio
import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from cyoa.llm.broker import MemoryEntry
from cyoa.llm.pipeline import (
    DirectiveComponent,
    GoalComponent,
    HistoryComponent,
    MemoryComponent,
    PersonaComponent,
    PlayerSheetComponent,
    PromptComponent,
    PromptComponentMixin,
    PromptPipeline,
    SummarizationComponent,
    SystemMessageComponent,
)
from cyoa.llm.providers import (
    LlamaCppProvider,
    count_messages_tokens,
)


class _InjectHarness(PromptComponentMixin):
    pass


class _AppendComponent(PromptComponent):
    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def transform(self, context, messages):
        return messages + [{"role": self.role, "content": self.content}]

# ── LlamaCppProvider Tests ───────────────────────────────────────────────────

@pytest.fixture
def mock_llama():
    with patch("cyoa.llm.providers.Llama") as mock:
        instance = mock.return_value
        instance.tokenize.return_value = [1, 2, 3] # 3 tokens

        def mock_cc(*args, **kwargs):
            if kwargs.get("stream"):
                return [{"choices": [{"delta": {"content": '{"narrative": "Test"}'}}]}]
            return {
                "choices": [{"message": {"content": '{"narrative": "Test"}'}}]
            }
        instance.create_chat_completion.side_effect = mock_cc

        yield mock

def test_llama_cpp_token_count(mock_llama):
    provider = LlamaCppProvider(model_path="dummy.gguf")
    count = provider.count_tokens("Hello world")
    assert count == 3
    mock_llama.return_value.tokenize.assert_called_once()


def test_count_messages_tokens_handles_missing_keys() -> None:
    messages = [{"role": "system"}, {"content": "hello"}, {}]
    assert count_messages_tokens(messages, lambda text: len(text)) == len("system") + len("hello")


def test_mock_provider_count_tokens_in_messages_uses_base_helper() -> None:
    from cyoa.llm.providers import MockProvider

    provider = MockProvider()
    messages = [{"role": "user", "content": "abcd1234"}]
    assert provider.count_tokens_in_messages(messages) == (len("user") // 4) + (len("abcd1234") // 4)


def test_llama_cpp_token_count_returns_zero_for_empty_text(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    assert provider.count_tokens("") == 0
    mock_llama.return_value.tokenize.assert_not_called()


def test_llama_cpp_token_count_falls_back_when_tokenizer_errors(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    mock_llama.return_value.tokenize.side_effect = RuntimeError("boom")

    text = "tokenizer failed"
    assert provider.count_tokens(text) == len(text) // 4


def test_llama_cpp_extract_stream_token_handles_malformed_chunks(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")

    assert provider._extract_stream_token("bad") == ""
    assert provider._extract_stream_token({"choices": []}) == ""
    assert provider._extract_stream_token({"choices": [{"delta": {"content": 123}}]}) == ""


def test_llama_cpp_prepare_stream_params_omits_optional_fields(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    params = provider._prepare_stream_params([{"role": "user", "content": "hi"}], None, 12, 0.3)
    assert params == {
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 12,
        "temperature": 0.3,
        "stream": True,
    }


def test_llama_cpp_build_json_repair_messages_appends_instruction(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    schema = {"type": "object", "required": ["narrative"]}

    repaired = provider._build_json_repair_messages(
        [{"role": "user", "content": "Tell me a story"}],
        schema,
    )

    assert repaired[:-1] == [{"role": "user", "content": "Tell me a story"}]
    assert repaired[-1]["role"] == "user"
    assert "ONLY a valid JSON object" in repaired[-1]["content"]
    assert '"required":["narrative"]' in repaired[-1]["content"]


def test_llama_cpp_stream_completion_retries_without_response_format_on_runtime_error(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    cancel_event = threading.Event()
    schema = {"type": "object"}
    streamed = [{"choices": [{"delta": {"content": '{"narrative":"Recovered"}'}}]}]

    mock_llama.return_value.create_chat_completion.side_effect = [
        RuntimeError("Unexpected empty grammar stack after accepting piece: @entries"),
        streamed,
    ]

    result = list(
        provider._stream_completion(
            [{"role": "user", "content": "hi"}],
            schema,
            32,
            0.2,
            cancel_event,
        )
    )

    assert result == streamed
    assert mock_llama.return_value.create_chat_completion.call_count == 2

    first_call = mock_llama.return_value.create_chat_completion.call_args_list[0].kwargs
    second_call = mock_llama.return_value.create_chat_completion.call_args_list[1].kwargs

    assert first_call["response_format"] == {"type": "json_object", "schema": schema}
    assert "response_format" not in second_call
    assert second_call["messages"][-1]["role"] == "user"
    assert "ONLY a valid JSON object" in second_call["messages"][-1]["content"]


def test_provider_capabilities_are_normalized(mock_llama) -> None:
    from cyoa.llm.providers import MockProvider

    llama = LlamaCppProvider(model_path="dummy.gguf")
    mock = MockProvider()

    assert llama.capabilities().state_transfer is True
    assert llama.capabilities().streaming_json is True
    assert mock.capabilities().structured_json is True

@pytest.mark.asyncio
async def test_llama_cpp_generate_json(mock_llama):
    provider = LlamaCppProvider(model_path="dummy.gguf")
    schema = {"type": "object"}
    messages = [{"role": "user", "content": "hi"}]

    result = await provider.generate_json(messages, schema, temperature=0.5)

    assert result == '{"narrative": "Test"}'

    # In generate_json, it actually calls stream and joins
    mock_llama.return_value.create_chat_completion.assert_called()

@pytest.mark.asyncio
async def test_llama_cpp_stream_json(mock_llama):
    provider = LlamaCppProvider(model_path="dummy.gguf")

    # Mock streaming output
    mock_stream = [
        {"choices": [{"delta": {"content": '{"narr'}}] },
        {"choices": [{"delta": {"content": 'ative": "Test"'}}] },
        {"choices": [{"delta": {"content": "}"}}] }
    ]
    mock_llama.return_value.create_chat_completion.side_effect = None
    mock_llama.return_value.create_chat_completion.return_value = mock_stream

    chunks = []
    async for chunk in provider.stream_json([{"role": "user", "content": "hi"}], {}):
        chunks.append(chunk)

    # Combined chunks should result in '{"narrative": "Test"}'
    assert "".join(chunks) == '{"narrative": "Test"}'


@pytest.mark.asyncio
async def test_llama_cpp_generate_text(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    text = await provider.generate_text([{"role": "user", "content": "hi"}], max_tokens=10, temperature=0.2)
    assert text == '{"narrative": "Test"}'


@pytest.mark.asyncio
async def test_llama_cpp_streaming_uses_daemon_thread(mock_llama, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeThread:
        def __init__(self, *, target, args, name, daemon):
            captured["target"] = target
            captured["args"] = args
            captured["name"] = name
            captured["daemon"] = daemon
            self._target = target
            self._args = args

        def start(self) -> None:
            self._target(*self._args)

        def join(self, timeout: float | None = None) -> None:
            return None

    monkeypatch.setattr("cyoa.llm.providers.threading.Thread", FakeThread)
    provider = LlamaCppProvider(model_path="dummy.gguf")

    result = await provider.generate_text([{"role": "user", "content": "hi"}], max_tokens=10)

    assert result == '{"narrative": "Test"}'
    assert captured["daemon"] is True
    assert captured["name"] == "llama-cpp-stream"


@pytest.mark.asyncio
async def test_llama_cpp_save_and_load_state(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    mock_llama.return_value.save_state.return_value = b"kv-state"

    assert await provider.save_state() == b"kv-state"
    await provider.load_state(b"kv-state")

    mock_llama.return_value.save_state.assert_called_once()
    mock_llama.return_value.load_state.assert_called_once_with(b"kv-state")


@pytest.mark.asyncio
async def test_llama_cpp_state_operations_swallow_failures(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    mock_llama.return_value.save_state.side_effect = RuntimeError("save failed")
    mock_llama.return_value.load_state.side_effect = RuntimeError("load failed")

    assert await provider.save_state() is None
    await provider.load_state(b"bad-state")


def test_llama_cpp_close_releases_model_when_idle(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    provider.close()
    assert not hasattr(provider, "llm")


def test_llama_cpp_close_skips_cleanup_when_lock_is_held(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    provider._lock.acquire()
    try:
        provider.close()
        assert hasattr(provider, "llm")
    finally:
        provider._lock.release()


def test_llama_cpp_close_signals_active_streams(mock_llama) -> None:
    provider = LlamaCppProvider(model_path="dummy.gguf")
    cancel_event = threading.Event()
    provider._register_cancel_event(cancel_event)

    provider.close()

    assert cancel_event.is_set()
    assert not hasattr(provider, "llm")


@pytest.mark.asyncio
async def test_llama_cpp_close_waits_for_cancelled_stream_cleanup(mock_llama) -> None:
    mock_llama.return_value.token_eos.return_value = 2
    provider = LlamaCppProvider(model_path="dummy.gguf")
    stream_started = threading.Event()
    allow_cleanup = threading.Event()

    def slow_cancelable_gen(*args, **kwargs):
        processor = kwargs["logits_processor"][0]
        yield {"choices": [{"delta": {"content": "first"}}]}
        stream_started.set()
        while not processor.cancel_event.is_set():
            time.sleep(0.01)
        allow_cleanup.wait(timeout=1.0)

    mock_llama.return_value.create_chat_completion.side_effect = slow_cancelable_gen

    task = asyncio.create_task(provider.generate_text([{"role": "user", "content": "hi"}]))
    await asyncio.to_thread(stream_started.wait, 1.0)

    close_task = asyncio.create_task(asyncio.to_thread(provider.close))
    await asyncio.sleep(0.1)
    assert not close_task.done()

    allow_cleanup.set()
    await asyncio.wait_for(close_task, timeout=1.0)
    await asyncio.wait_for(task, timeout=1.0)

    assert not hasattr(provider, "llm")

# ── MockProvider Tests ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_provider_generate_text_summary_branch() -> None:
    from cyoa.llm.providers import MockProvider

    provider = MockProvider()
    result = await provider.generate_text([{"role": "user", "content": "Please summarize this arc"}])
    assert "digital mists" in result


@pytest.mark.asyncio
async def test_mock_provider_generate_json_extraction_branch() -> None:
    from cyoa.llm.providers import MockProvider

    provider = MockProvider()
    data = json.loads(await provider.generate_json([], {"required": ["stat_updates"]}))
    assert data["stat_updates"] == {"reputation": 1}
    assert data["items_gained"] == ["Static Spark"]


@pytest.mark.asyncio
async def test_mock_provider_generate_json():
    from cyoa.llm.providers import MockProvider

    provider = MockProvider()
    schema = {"type": "object"}
    messages = [{"role": "user", "content": "hi"}]

    result = await provider.generate_json(messages, schema)
    data = json.loads(result)

    assert "narrative" in data
    assert "choices" in data
    assert len(data["choices"]) >= 2
    assert "The Mockingbird" in data["npcs_present"]


@pytest.mark.asyncio
async def test_mock_provider_stream_json():
    from cyoa.llm.providers import MockProvider

    provider = MockProvider()
    chunks = []
    async for chunk in provider.stream_json([{"role": "user", "content": "hi"}], {}):
        chunks.append(chunk)

    full_json = "".join(chunks)
    data = json.loads(full_json)
    assert "narrative" in data
    assert "digital void" in data["narrative"]


def test_model_broker_rejects_missing_llama_cpp_model() -> None:
    from cyoa.llm.broker import ModelBroker

    with pytest.raises(FileNotFoundError, match="model file does not exist"):
        ModelBroker(model_path="completely_non_existent_model_1212.gguf")


@pytest.mark.smoke
def test_model_broker_uses_mock_provider_when_requested() -> None:
    from cyoa.llm.broker import ModelBroker
    from cyoa.llm.providers import MockProvider

    with patch.dict(os.environ, {"LLM_PROVIDER": "mock"}, clear=False):
        broker = ModelBroker()

    assert isinstance(broker.provider, MockProvider)


def test_model_broker_uses_llama_cpp_provider_when_model_exists(tmp_path: Path, mock_llama) -> None:
    from cyoa.llm.broker import ModelBroker

    model_path = tmp_path / "model.gguf"
    model_path.write_bytes(b"gguf")

    with patch.dict(os.environ, {"LLM_PROVIDER": "llama_cpp"}, clear=False):
        broker = ModelBroker(model_path=str(model_path))

    assert isinstance(broker.provider, LlamaCppProvider)


def test_llama_cpp_token_count_falls_back_when_lock_is_busy(mock_llama):
    provider = LlamaCppProvider(model_path="dummy.gguf")
    provider._lock.acquire()
    try:
        text = "busy lock text"
        assert provider.count_tokens(text) == len(text) // 4
    finally:
        provider._lock.release()


# ── Prompt Pipeline Tests ────────────────────────────────────────────────────


def test_prompt_pipeline_processes_components_in_order() -> None:
    pipeline = PromptPipeline()
    pipeline.add_component(_AppendComponent("system", "one"))
    pipeline.add_component(_AppendComponent("user", "two"))

    result = pipeline.process(context=None)
    assert result == [
        {"role": "system", "content": "one"},
        {"role": "user", "content": "two"},
    ]


def test_system_message_component_supports_static_and_template_modes() -> None:
    static_component = SystemMessageComponent(static_content="Static system")
    messages = static_component.transform(None, [{"role": "user", "content": "start"}])
    assert messages[0] == {"role": "system", "content": "Static system"}

    template = MagicMock()
    template.render.return_value = "Rendered summary"
    context = SimpleNamespace(
        jinja_env=MagicMock(get_template=MagicMock(return_value=template)),
        inventory=["Key"],
        player_stats={"health": 5},
        memories=["Seen before"],
        scene_summary="scene",
        chapter_summary="chapter",
        arc_summary="arc",
    )
    component = SystemMessageComponent(template_name="system_prompt.j2")
    appended = component.transform(context, [{"role": "system", "content": "Base"}])

    assert appended[0]["content"] == "Base\n\nRendered summary"
    context.jinja_env.get_template.assert_called_once_with("system_prompt.j2")


def test_prompt_component_mixin_handles_empty_text_and_empty_system() -> None:
    harness = _InjectHarness()
    untouched = [{"role": "user", "content": "hello"}]
    assert harness._inject_into_system(untouched, "") == untouched

    messages = [{"role": "system", "content": "   "}]
    updated = harness._inject_into_system(messages, " inserted ")
    assert updated[0]["content"] == "inserted"


def test_persona_component_injects_default_persona() -> None:
    messages = PersonaComponent().transform(None, [])
    assert messages[0]["role"] == "system"
    assert "Narrative Persona" in messages[0]["content"]
    assert "strictly valid JSON" in messages[0]["content"]


def test_player_sheet_component_renders_empty_inventory_and_stats() -> None:
    context = SimpleNamespace(inventory=[], player_stats={"gold": 7})
    messages = PlayerSheetComponent().transform(context, [])
    assert "Current Inventory: Empty" in messages[0]["content"]
    assert '"gold": 7' in messages[0]["content"]


def test_memory_component_formats_multiple_memories_and_noop_on_empty() -> None:
    component = MemoryComponent()
    original = [{"role": "user", "content": "hi"}]
    assert component.transform(SimpleNamespace(memories=[]), list(original)) == original

    rendered = component.transform(SimpleNamespace(memories=["m1", "m2"]), [])
    assert "<memory_retrieval>" in rendered[0]["content"]
    assert "\n---\n" in rendered[0]["content"]

    structured = component.transform(
        SimpleNamespace(
            memories=[],
            memory_entries=[
                MemoryEntry(
                    text="The gate shudders from the last explosion.",
                    category="scene",
                    reason="Recent scene continuity.",
                ),
                MemoryEntry(
                    text="Mira still distrusts the regent.",
                    category="entity",
                    source="Mira",
                    reason="Mira is present in the current scene.",
                ),
            ],
        ),
        [],
    )
    assert "[Scene Memory]" in structured[0]["content"]
    assert "[Entity Memory: Mira]" in structured[0]["content"]
    assert "Reason: Mira is present in the current scene." in structured[0]["content"]


def test_summarization_component_renders_all_levels_and_noops_without_data() -> None:
    component = SummarizationComponent()
    messages = [{"role": "user", "content": "hi"}]
    assert component.transform(SimpleNamespace(), list(messages)) == messages

    rendered = component.transform(
        SimpleNamespace(scene_summary="scene", chapter_summary="chapter", arc_summary="arc"),
        [],
    )
    assert "<arc_summary>" in rendered[0]["content"]
    assert "<chapter_summary>" in rendered[0]["content"]
    assert "<scene_summary>" in rendered[0]["content"]


def test_goal_and_directive_components_render_lists() -> None:
    goal_messages = GoalComponent().transform(SimpleNamespace(goals=["Find relic"]), [])
    directive_messages = DirectiveComponent().transform(
        SimpleNamespace(directives=["No combat", "Favor stealth"]),
        [],
    )

    assert "## Current Narrative Goals" in goal_messages[0]["content"]
    assert "- Find relic" in goal_messages[0]["content"]
    assert "## Active Directives" in directive_messages[0]["content"]
    assert "! No combat" in directive_messages[0]["content"]


def test_history_component_appends_history() -> None:
    component = HistoryComponent()
    messages = [{"role": "system", "content": "sys"}]
    context = SimpleNamespace(history=[{"role": "user", "content": "u"}])
    assert component.transform(context, messages) == [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "u"},
    ]
