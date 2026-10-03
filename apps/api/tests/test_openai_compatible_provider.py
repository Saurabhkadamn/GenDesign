import asyncio
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


@pytest.mark.asyncio
async def test_streaming_provider_has_a_total_wall_clock_deadline(monkeypatch):
    class SlowStreamResponse(_StreamResponse):
        async def aiter_lines(self):
            while True:
                # Keep each read active so an HTTPX inactivity timeout alone
                # would never stop this response.
                await asyncio.sleep(0.01)
                yield 'data: {"choices":[{"delta":{"content":"thinking"}}]}'

    class SlowStreamingClient:
        def stream(self, method, url, *, headers, json, timeout):
            return SlowStreamResponse([])

    monkeypatch.setattr(openai_compatible.db, "client", lambda: SlowStreamingClient())
    monkeypatch.setattr(openai_compatible, "_request_deadline_seconds", lambda: 0.04)
    with pytest.raises(openai_compatible.ModelFailure) as error:
        await openai_compatible.turn({
            "provider": "openai_compatible", "base_url": "https://inference.baseten.co/v1",
            "model_id": "deepseek-ai/DeepSeek-V4.1-Flash", "api_key": "secret", "stream": True,
        }, [{"role": "user", "content": "Call the check."}], [], max_tokens=2048)
    assert error.value.category == "timeout"


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

class GeminiThoughtResponse:
    status_code = 200
    text = json.dumps({
        "choices": [{"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-gemini", "type": "function", "extra_content": {
                "google": {"thought_signature": "opaque-signature"}},
            "function": {"name": "apply_changes", "arguments": '{"files":{}}'}}
        ]}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 4},
    })

    @property
    def is_success(self):
        return True


class GeminiThoughtClient:
    async def post(self, url, *, headers, json, timeout):
        return GeminiThoughtResponse()


class TokenLimitClient:
    def __init__(self, accepted_limit):
        self.accepted_limit = accepted_limit
        self.payloads = []

    async def post(self, url, *, headers, json, timeout):
        self.payloads.append(json)
        if json["max_tokens"] > self.accepted_limit:
            return FallbackResponse(400, {"error": {
                "message": f"max_tokens must be no more than {self.accepted_limit}"}})
        return FakeResponse()


def test_openai_compatible_default_budget_allows_model_maximum(monkeypatch):
    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS", raising=False)
    assert openai_compatible._output_tokens() == 131072


def test_gemini_37_uses_high_reasoning_and_google_default_temperature():
    config = {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.7-flash",
    }
    assert openai_compatible._reasoning_effort(config) == "high"
    assert openai_compatible._sampling(config) == (1.0, None)


def test_gemini_output_cap_can_match_documented_model_maximum(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS", "65536")
    assert openai_compatible._output_tokens(131072) == 65536


def test_gemini_rpm_defaults_to_four_and_can_be_overridden(monkeypatch):
    config = {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.7-flash",
    }
    monkeypatch.delenv("GEMINI_AI_STUDIO_REQUESTS_PER_MINUTE", raising=False)
    assert openai_compatible._requests_per_minute(config) == 4
    monkeypatch.setenv("GEMINI_AI_STUDIO_REQUESTS_PER_MINUTE", "3")
    assert openai_compatible._requests_per_minute(config) == 3


@pytest.mark.asyncio
async def test_gemini_requests_are_spaced_to_stay_under_four_per_minute(monkeypatch):
    config = {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.7-flash",
    }
    clock = [100.0]
    waits = []

    def monotonic():
        return clock[0]

    async def sleep(seconds):
        waits.append(seconds)
        clock[0] += seconds

    monkeypatch.setattr(openai_compatible.time, "monotonic", monotonic)
    monkeypatch.setattr(openai_compatible.asyncio, "sleep", sleep)
    monkeypatch.setattr(openai_compatible, "_gemini_request_lock", openai_compatible.threading.Lock())
    monkeypatch.setattr(openai_compatible, "_gemini_next_request_at", 0.0)

    for _ in range(5):
        await openai_compatible._pace_request(config)

    assert len(waits) == 4
    assert all(wait >= 15.0 for wait in waits)


@pytest.mark.asyncio
async def test_openai_compatible_steps_down_only_when_provider_rejects_max_tokens(monkeypatch):
    client = TokenLimitClient(accepted_limit=8192)
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    monkeypatch.delenv("OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS", raising=False)
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://ai-gateway.vercel.sh/v1",
        "model_id": "xiaomi/mimo-v2.6-flash", "api_key": "secret", "stream": False,
        "max_output_tokens": 131072,
    }, [{"role": "user", "content": "Call the check."}], [{"type": "function", "function": {
        "name": "connection_check", "parameters": {"type": "object"}}}], max_tokens=None)

    assert [payload["max_tokens"] for payload in client.payloads] == [
        131072, 65536, 32768, 16384, 8192,
    ]
    assert result["calls"][0]["name"] == "connection_check"


def test_nebius_glm_requires_a_tool_action():
    assert openai_compatible._tool_choice({
        "base_url": "https://api.tokenfactory.us-central1.nebius.com/v1",
        "model_id": "zai-org/GLM-5.3-Flash",
    }, [{"type": "function", "function": {"name": "build"}}]) == "required"


def test_provider_failures_keep_http_status_for_retry_routing():
    rate_limited = openai_compatible._failure(429, '{"error":"RESOURCE_EXHAUSTED"}')
    overloaded = openai_compatible._failure(503, '{"error":"UNAVAILABLE"}')
    assert (rate_limited.category, rate_limited.status_code) == ("rate_limit", 429)
    assert (overloaded.category, overloaded.status_code) == ("overloaded", 503)


def test_nebius_deepseek_uses_high_reasoning_effort():
    assert openai_compatible._reasoning_effort({
        "base_url": "https://api.tokenfactory.us-central1.nebius.com/v1",
        "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731",
    }) == "high"


def test_nebius_global_v41_uses_required_tools_and_high_reasoning():
    config = {"base_url": "https://api.tokenfactory.nebius.com/v1",
              "model_id": "deepseek-ai/DeepSeek-V4.1-Flash", "api_key": "secret"}
    assert openai_compatible._tool_choice(config, [
        {"type": "function", "function": {"name": "apply_changes"}}]) == "required"
    assert openai_compatible._reasoning_effort(config) == "high"
    assert openai_compatible._fallback_config(config)["model_id"] == "MiniMaxAI/MiniMax-M3"


def test_nebius_minimax_is_the_deepseek_fallback():
    fallback = openai_compatible._fallback_config({
        "base_url": "https://api.tokenfactory.us-central1.nebius.com/v1",
        "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731", "api_key": "secret",
    })
    assert fallback["model_id"] == "MiniMaxAI/MiniMax-M3"
    assert openai_compatible._reasoning_effort(fallback) == "high"


def test_nebius_explicit_retry_switches_to_minimax():
    retry = openai_compatible.retry_config({
        "base_url": "https://api.tokenfactory.us-central1.nebius.com/v1",
        "model_id": "deepseek-ai/DeepSeek-V4-Flash-0731", "api_key": "secret",
    })
    assert retry["model_id"] == "MiniMaxAI/MiniMax-M3"
    assert retry["fallback_for"] == "deepseek-ai/DeepSeek-V4-Flash-0731"
    assert retry["stream"] is False
    assert openai_compatible._reasoning_effort(retry) == "high"


def test_request_timeout_is_configurable_for_high_effort_turns(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "780")
    assert openai_compatible._request_timeout().read == 780
    assert openai_compatible._request_deadline_seconds() == 780


def test_request_timeout_is_bounded(monkeypatch):
    monkeypatch.setenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "9999")
    assert openai_compatible._request_timeout().read == 900
    assert openai_compatible._request_deadline_seconds() == 900


def test_request_timeout_leaves_room_for_vercel_checkpointing(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "780")
    assert openai_compatible._request_deadline_seconds() == 240


@pytest.mark.asyncio
async def test_openai_compatible_preserves_gemini_thought_signature(monkeypatch):
    monkeypatch.setattr(openai_compatible.db, "client", lambda: GeminiThoughtClient())
    result = await openai_compatible.turn({
        "provider": "openai_compatible", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.8-flash", "api_key": "secret", "stream": False,
    }, [{"role": "user", "content": "Call the tool."}], [{"type": "function", "function": {
        "name": "apply_changes", "parameters": {"type": "object"}}}], max_tokens=1024)
    assert result["message"]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque-signature"}}


@pytest.mark.asyncio
async def test_gemini_tries_saved_keys_in_order_only_after_429(monkeypatch):
    used_keys = []

    async def fake_turn_once(config, messages, tools, **kwargs):
        used_keys.append(config["api_key"])
        if config["api_key"] != "key-three-secret":
            raise openai_compatible.ModelFailure("rate_limit", "HTTP 429", status_code=429)
        return {"message": {"role": "assistant", "content": "ready"}, "calls": []}

    monkeypatch.setattr(openai_compatible, "_turn_once", fake_turn_once)
    result = await openai_compatible.turn({
        "provider": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.7-flash", "api_key": "key-one-secret",
        "api_key_fallbacks": ["key-two-secret", "key-three-secret"], "stream": False,
    }, [{"role": "user", "content": "Reply."}], [], max_tokens=1024, allow_fallback=False)

    assert used_keys == ["key-one-secret", "key-two-secret", "key-three-secret"]
    assert result["credential_fallback"] == {"key_index": 3, "reason": "http_429"}


@pytest.mark.asyncio
async def test_gemini_does_not_rotate_keys_after_non_429_failure(monkeypatch):
    used_keys = []

    async def fake_turn_once(config, messages, tools, **kwargs):
        used_keys.append(config["api_key"])
        raise openai_compatible.ModelFailure("overloaded", "HTTP 500", status_code=500)

    monkeypatch.setattr(openai_compatible, "_turn_once", fake_turn_once)
    with pytest.raises(openai_compatible.ModelFailure, match="HTTP 500"):
        await openai_compatible.turn({
            "provider": "openai_compatible",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
            "model_id": "gemini-3.7-flash", "api_key": "key-one-secret",
            "api_key_fallbacks": ["key-two-secret", "key-three-secret"], "stream": False,
        }, [{"role": "user", "content": "Reply."}], [], max_tokens=1024)

    assert used_keys == ["key-one-secret"]


@pytest.mark.asyncio
async def test_gemini_retries_503_once_with_the_same_key(monkeypatch):
    used_keys = []
    delays = []

    async def fake_turn_once(config, messages, tools, **kwargs):
        used_keys.append(config["api_key"])
        if len(used_keys) == 1:
            raise openai_compatible.ModelFailure("overloaded", "HTTP 503", status_code=503)
        return {"message": {"role": "assistant", "content": "ready"}, "calls": []}

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(openai_compatible, "_turn_once", fake_turn_once)
    monkeypatch.setattr(openai_compatible.asyncio, "sleep", fake_sleep)
    result = await openai_compatible.turn({
        "provider": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.7-flash", "api_key": "key-one-secret",
        "api_key_fallbacks": ["key-two-secret", "key-three-secret"], "stream": False,
    }, [{"role": "user", "content": "Reply."}], [], max_tokens=1024)

    assert used_keys == ["key-one-secret", "key-one-secret"]
    assert delays == [openai_compatible.GEMINI_503_RETRY_DELAY_SECONDS]
    assert result["provider_retry"] == {"attempts": 1, "reason": "http_503_same_key"}


@pytest.mark.asyncio
async def test_gemini_rotates_saved_keys_after_persistent_503(monkeypatch):
    used_keys = []

    async def fake_turn_once(config, messages, tools, **kwargs):
        used_keys.append(config["api_key"])
        if config["api_key"] != "key-three-secret":
            raise openai_compatible.ModelFailure("overloaded", "HTTP 503", status_code=503)
        return {"message": {"role": "assistant", "content": "ready"}, "calls": []}

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(openai_compatible, "_turn_once", fake_turn_once)
    monkeypatch.setattr(openai_compatible.asyncio, "sleep", fake_sleep)
    result = await openai_compatible.turn({
        "provider": "openai_compatible",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model_id": "gemini-3.8-flash", "api_key": "key-one-secret",
        "api_key_fallbacks": ["key-two-secret", "key-three-secret"], "stream": False,
    }, [{"role": "user", "content": "Reply."}], [], max_tokens=1024)

    assert used_keys == ["key-one-secret", "key-one-secret",
                         "key-two-secret", "key-three-secret"]
    assert result["credential_fallback"] == {"key_index": 3, "reason": "http_503"}
