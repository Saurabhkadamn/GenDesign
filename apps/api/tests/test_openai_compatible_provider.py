import json

import pytest

from forma_api.providers import openai_compatible


class FakeResponse:
    status_code = 200
    text = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-1", "type": "function", "function": {
                "name": "connection_check", "arguments": '{"value":"ready"}'}}
        ]}}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 7},
    })

    @property
    def is_success(self):
        return True


class FakeClient:
    def __init__(self):
        self.url = None
        self.payload = None

    async def post(self, url, *, headers, json, timeout):
        self.url = url
        self.payload = json
        assert headers["Authorization"].startswith("Bearer ")
        return FakeResponse()


class FallbackResponse:
    def __init__(self, status_code, body):
        self.status_code = status_code
        self.text = json.dumps(body)

    @property
    def is_success(self):
        return 200 <= self.status_code < 300


class _StreamResponse:
    def __init__(self, events):
        self.status_code = 200
        self._events = events
        self._body = b""

    @property
    def is_success(self):
        return True

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def aiter_lines(self):
        for event in self._events:
            yield "data: " + json.dumps(event)


class FallbackClient:
    def __init__(self):
        self.payloads = []

    async def post(self, url, *, headers, json, timeout):
        self.payloads.append(json)
        return FallbackResponse(503, {"error": {"message": "temporarily overloaded", "code": 503}})

    def stream(self, method, url, *, headers, json, timeout):
        self.payloads.append(json)
        return _StreamResponse([{
            "choices": [{"delta": {"role": "assistant", "content": "fallback-ready"}}],
            "usage": {"prompt_tokens": 4, "completion_tokens": 2},
        }])


@pytest.mark.asyncio
async def test_openai_compatible_turn_uses_generic_chat_contract(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://integrate.api.nvidia.com/v1",
        "model_id": "deepseek-ai/deepseek-v4-pro-0813", "api_key": "secret", "stream": False,
    }, [{"role": "user", "content": "Call the check."}], [{"type": "function", "function": {
        "name": "connection_check", "parameters": {"type": "object"}}}], max_tokens=2048)
    assert client.url == "https://integrate.api.nvidia.com/v1/chat/completions"
    assert client.payload["model"] == "deepseek-ai/deepseek-v4-pro-0813"
    assert client.payload["tool_choice"] == "auto"
    assert "provider" not in client.payload
    assert client.payload["chat_template_kwargs"] == {"thinking": False}
    assert result["calls"] == [{"id": "call-1", "name": "connection_check", "input": {"value": "ready"}}]
    assert result["inputTokens"] == 12


@pytest.mark.asyncio
async def test_openai_compatible_connection_budget_is_sent_when_no_request_override(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://example.com/v1",
        "model_id": "provider/arbitrary-model", "api_key": "secret", "stream": False,
        "max_output_tokens": 777,
    }, [{"role": "user", "content": "Call the check."}], [], max_tokens=None)
    assert client.payload["max_tokens"] == 777


@pytest.mark.asyncio
async def test_baseten_deepseek_uses_high_reasoning_with_tools(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://inference.baseten.co/v1",
        "model_id": "deepseek-ai/DeepSeek-V4.1-Flash", "api_key": "secret", "stream": False,
    }, [{"role": "user", "content": "Call the check."}], [{"type": "function", "function": {
        "name": "connection_check", "parameters": {"type": "object"}}}], max_tokens=2048)
    assert client.payload["reasoning_effort"] == "high"
    assert client.payload["tool_choice"] == "auto"
    assert result["calls"][0]["name"] == "connection_check"


@pytest.mark.asyncio
async def test_baseten_deepseek_streams_reasoning_and_tool_calls_by_default(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_STREAM", "false")
    class StreamingClient:
        def __init__(self):
            self.payload = None

        def stream(self, method, url, *, headers, json, timeout):
            self.payload = json
            return _StreamResponse([
                {"choices": [{"delta": {"role": "assistant", "reasoning_content": "checking"}}]},
                {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call-1",
                    "type": "function", "function": {"name": "connection_check",
                    "arguments": '{"value":"ready"}'}}]}}]},
                {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 7}},
            ])

    client = StreamingClient()
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://inference.baseten.co/v1",
        "model_id": "deepseek-ai/DeepSeek-V4.1-Flash", "api_key": "secret",
    }, [{"role": "user", "content": "Call the check."}], [{"type": "function", "function": {
        "name": "connection_check", "parameters": {"type": "object"}}}], max_tokens=2048)
    assert client.payload["stream"] is True
    assert client.payload["reasoning_effort"] == "high"
    assert result["calls"][0]["input"] == {"value": "ready"}
    assert result["outputTokens"] == 7
    assert openai_compatible._stream_enabled({
        "base_url": "https://inference.baseten.co/v1",
        "model_id": "deepseek-ai/DeepSeek-V4.1-Flash",
        "stream": False,
    }) is False


def test_openai_compatible_base_url_rejects_embedded_credentials():
    with pytest.raises(openai_compatible.ModelFailure):
        openai_compatible.base_url({"base_url": "https://user:pass@example.com/v1"})


def test_openai_compatible_timeout_honors_bounded_provider_setting(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "270")
    assert openai_compatible._request_timeout().read == 270
    monkeypatch.setenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "invalid")
    assert openai_compatible._request_timeout().read == 240


def test_recover_json_tool_list_from_text_only_provider_response():
    tools = [{"type": "function", "function": {"name": "read_file"}}]
    recovered = openai_compatible._recover_text_tool_call(
        '[[{"name":"read_file","parameters":{"path":"parts/block.py"}}]]', tools)
    assert recovered["name"] == "read_file"
    assert recovered["input"] == {"path": "parts/block.py"}


def test_stream_deltas_reconstruct_reasoning_and_tool_arguments():
    second_argument = '"ready"}'
    events = [
        {"choices": [{"delta": {"role": "assistant", "reasoning_content": "think "}}]},
        {"choices": [{"delta": {"reasoning_content": "then", "content": "done",
            "tool_calls": [{"index": 0, "id": "call-", "type": "function",
                "function": {"name": "connection_check", "arguments": '{"value":'}}]}}]},
        {"choices": [{"delta": {"content": "", "tool_calls": [{"index": 0, "id": "1",
            "function": {"arguments": second_argument}}]}}]},
    ]
    merged = openai_compatible._merge_stream(events)
    message = merged["choices"][0]["message"]
    assert message["reasoning_content"] == "think then"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"value":"ready"}'


@pytest.mark.asyncio
async def test_openai_compatible_web_search_is_explicitly_unsupported():
    with pytest.raises(openai_compatible.ModelFailure, match="web search"):
        await openai_compatible.turn({"base_url": "https://example.com/v1", "model_id": "x", "api_key": "secret"},
                                     [], [], web_search=True, max_searches=1)


@pytest.mark.asyncio
async def test_nvidia_kimi_uses_nemotron_as_first_fallback(monkeypatch):
    client = FallbackClient()
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://integrate.api.nvidia.com/v1",
        "model_id": "moonshotai/kimi-k3", "api_key": "secret", "stream": False,
    }, [{"role": "user", "content": "Reply with one word."}], [], max_tokens=1024)

    assert result["message"]["content"] == "fallback-ready"
    assert result["fallback"] == {
        "from": "moonshotai/kimi-k3",
        "to": "nvidia/nemotron-3-ultra-550b-a55b",
        "reason": "overloaded",
    }
    assert client.payloads[0]["model"] == "moonshotai/kimi-k3"
    assert client.payloads[1]["model"] == "nvidia/nemotron-3-ultra-550b-a55b"
    assert client.payloads[1]["chat_template_kwargs"]["enable_thinking"] is True


@pytest.mark.asyncio
async def test_vercel_gateway_uses_ordered_grok_tencent_alibaba_fallbacks(monkeypatch):
    calls = []

    async def fake_turn_once(config, messages, tools, **kwargs):
        calls.append(config["model_id"])
        if config["model_id"] != "alibaba/qwen3.8-max-0902":
            raise openai_compatible.ModelFailure("rate_limit", "limited")
        return {"message": {"role": "assistant", "content": "ready"}, "calls": []}

    monkeypatch.setattr(openai_compatible, "_turn_once", fake_turn_once)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://ai-gateway.vercel.sh/v1",
        "model_id": "spacexai/grok-4.6", "api_key": "secret", "stream": False,
    }, [{"role": "user", "content": "Reply."}], [], max_tokens=1024)

    assert calls == ["spacexai/grok-4.6", "tencent/hy4-preview", "alibaba/qwen3.8-max-0902"]
    assert result["fallback"] == {
        "from": "spacexai/grok-4.6",
        "to": "alibaba/qwen3.8-max-0902",
        "reason": "rate_limit",
    }
