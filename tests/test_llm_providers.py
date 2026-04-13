import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cyoa.llm.providers import LlamaCppProvider, OllamaProvider

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

# ── OllamaProvider Tests ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ollama_generate_json():
    messages = [{"role": "user", "content": "hi"}]
    schema = {"type": "object"}

    mock_response = MagicMock()
    mock_response.json.return_value = {"message": {"content": '{"narrative": "Ollama"}'}}
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response

        provider = OllamaProvider(model="llama3")
        result = await provider.generate_json(messages, schema)

        assert result == '{"narrative": "Ollama"}'
        # Verify payload
        args, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["model"] == "llama3"
        assert payload["format"] == schema
        assert payload["stream"] is False

@pytest.mark.asyncio
async def test_ollama_stream_json():
    # Helper for async iteration
    async def async_iter(items):
        for item in items:
            yield item

    # Use MagicMock for the response object so its methods don't return coroutines by default
    mock_response = MagicMock()
    mock_lines = [
        json.dumps({"message": {"content": '{"narr' }}),
        json.dumps({"message": {"content": 'ative": "Ollama"}'}}),
        json.dumps({"done": True})
    ]
    # aiter_lines should return an async iterator
    mock_response.aiter_lines.return_value = async_iter(mock_lines)
    mock_response.raise_for_status = MagicMock()

    mock_context = MagicMock()
    # __aenter__ must return the response
    mock_context.__aenter__ = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient.stream", return_value=mock_context):
        provider = OllamaProvider(model="llama3")
        chunks = []
        async for chunk in provider.stream_json([{"role": "user", "content": "hi"}], {}):
            chunks.append(chunk)

        assert "".join(chunks) == '{"narrative": "Ollama"}'


@pytest.mark.asyncio
async def test_ollama_stream_json_skips_malformed_lines():
    async def async_iter(items):
        for item in items:
            yield item

    mock_response = MagicMock()
    mock_response.aiter_lines.return_value = async_iter(
        [
            "{not valid json",
            json.dumps({"message": {"content": "A"}}),
            json.dumps({"done": True}),
        ]
    )
    mock_response.raise_for_status = MagicMock()

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_response)

    with patch("httpx.AsyncClient.stream", return_value=mock_context):
        provider = OllamaProvider(model="llama3")
        chunks = []
        async for chunk in provider.stream_json([{"role": "user", "content": "hi"}], {}):
            chunks.append(chunk)

    assert "".join(chunks) == "A"


# ── MockProvider Tests ───────────────────────────────────────────────────────


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


@pytest.mark.asyncio
async def test_model_broker_fallback_to_mock():
    from cyoa.llm.broker import ModelBroker, StoryContext
    from cyoa.llm.providers import MockProvider

    # Use a non-existent path
    m_path = "completely_non_existent_model_1212.gguf"
    broker = ModelBroker(model_path=m_path)
    assert isinstance(broker.provider, MockProvider)

    ctx = StoryContext("start")
    node = await broker.generate_next_node_async(ctx)
    assert "digital void" in node.narrative


def test_llama_cpp_token_count_falls_back_when_lock_is_busy(mock_llama):
    provider = LlamaCppProvider(model_path="dummy.gguf")
    provider._lock.acquire()
    try:
        text = "busy lock text"
        assert provider.count_tokens(text) == len(text) // 4
    finally:
        provider._lock.release()
