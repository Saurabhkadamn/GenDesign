from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json

import httpx
import pytest

from forma_api import engine
from forma_api.providers import openrouter, openai_compatible
from forma_api.providers.cooldown import retry_after_seconds, with_cooldown
from forma_api.providers.capabilities import validate_candidate, validate_qualification
from forma_api.providers.provenance import require_qualified_identity
from forma_api.providers.tool_history import normalized_tool_history
from forma_api.agent_context import turn_with_context_recovery
from forma_api.graphs.design import protocol_safe_history


def test_saved_tool_history_replays_executed_json_and_preserves_signature():
    original = [{
        "role": "assistant",
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
        "tool_calls": [{
            "id": "call1", "type": "function",
            "function": {"name": "apply_changes", "arguments": '{"name":"old","name":"new"}'},
        }],
    }]

    replay = normalized_tool_history(original)

    assert replay[0]["tool_calls"][0]["function"]["arguments"] == '{"name":"new"}'
    assert replay[0]["reasoning_details"] == original[0]["reasoning_details"]
    assert original[0]["tool_calls"][0]["function"]["arguments"] == '{"name":"old","name":"new"}'


def test_saved_signed_tool_call_keeps_original_arguments():
    call = {"role": "assistant", "tool_calls": [{
        "id": "call1", "type": "function",
        "extra_content": {"google": {"thought_signature": "opaque"}},
        "function": {"name": "read_file", "arguments": '{"path":"part.py"}'},
    }]}
    assert normalized_tool_history([call]) == [call]
    call["tool_calls"][0]["function"]["arguments"] = '{"path":"old","path":"new"}'
    with pytest.raises(ValueError, match="signed tool call"):
        normalized_tool_history([call])


def test_saved_tool_history_rejects_unparseable_arguments():
    with pytest.raises(ValueError, match="invalid JSON"):
        normalized_tool_history([{"role": "assistant", "tool_calls": [{
            "function": {"name": "apply_changes", "arguments": "{broken"}
        }]}])


def test_invalid_provider_tool_output_can_retry_without_false_identity_failure():
    config = {"model_id": "pinned/model", "qualification": {"identityStable": True}}
    result = openrouter._tool_protocol_retry_result({"choices": []})
    assert result["protocolRetry"] is True
    assert require_qualified_identity(config, result) is result


def test_provider_cooldown_parses_seconds_and_http_date():
    now = datetime(2026, 10, 23, tzinfo=timezone.utc)
    assert retry_after_seconds("2.5") == 3
    assert retry_after_seconds("Fri, 23 Oct 2026 00:02:00 GMT", now=now) == 120
    assert retry_after_seconds("invalid") == 60
    assert retry_after_seconds("nan") == 60


def test_daily_free_quota_waits_for_reset_unless_provider_sets_deadline():
    now = datetime(2026, 10, 28, 13, 0, tzinfo=timezone.utc)
    error = openrouter.ModelFailure("quota", "Daily allowance reached")
    assert with_cooldown(error, {"retry_after": None}, now=now).retry_after_seconds == 11 * 3600 + 60
    assert with_cooldown(error, {"retry_after": "120"}, now=now).retry_after_seconds == 120


@pytest.mark.parametrize("failure", [openai_compatible._failure, openrouter.failure])
def test_provider_diagnostic_redacts_exact_connection_key(failure):
    key = "offline-connection-secret-without-a-known-prefix"
    error = failure(500, f'{{"message":"upstream echoed {key}"}}', secret=key)
    assert key not in error.diagnostic
    assert "[REDACTED]" in error.diagnostic
    assert error.category == "provider"


@pytest.mark.asyncio
async def test_compatible_transport_does_not_return_echoed_key_in_diagnostic(monkeypatch):
    key = "offline-transport-secret-without-a-known-prefix"

    async def handle(_request):
        return httpx.Response(500, json={"error": {"message": f"echoed {key}"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    try:
        with pytest.raises(openrouter.ModelFailure) as caught:
            await openai_compatible.turn(
                {"provider": "openai_compatible", "base_url": "https://api.example.com/v1",
                 "model_id": "test/model", "api_key": key},
                [{"role": "user", "content": "test"}], [], max_tokens=100,
            )
        assert key not in caught.value.diagnostic
        assert "[REDACTED]" in caught.value.diagnostic
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_provider_transport_redacts_error_before_trace_output(monkeypatch):
    key = "offline-trace-secret-without-a-known-prefix"

    async def handle(_request):
        return httpx.Response(500, json={"error": {"message": f"echoed {key}"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    try:
        compatible = await openai_compatible._chat(
            api_key=key, url="https://api.example.com/v1", model_id="test/model",
            messages=[{"role": "user", "content": "test"}], tools=[], output_tokens=100,
            extra_body={}, tool_choice="auto", stream=False, reasoning_effort=None,
            temperature=0, top_p=None,
        )
        router = await openrouter._openrouter_chat(
            api_key=key, model_id="test/model", messages=[{"role": "user", "content": "test"}],
            tools=[], token_parameter="max_tokens", output_tokens=100, provider={},
            web_search=False, max_searches=0,
        )
        for raw in (compatible, router):
            assert raw["status"] == 500
            assert key not in raw["body"]
            assert "[REDACTED]" in raw["body"]
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_compatible_throttle_keeps_provider_cooldown_without_fallback(monkeypatch):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            429, json={"error": {"message": "rate limit"}}, headers={"Retry-After": "123"}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    monkeypatch.setattr(openai_compatible.db, "client", lambda: client)
    try:
        with pytest.raises(openrouter.ModelFailure) as caught:
            await openai_compatible.turn(
                {
                    "provider": "openai_compatible",
                    "base_url": "https://api.tokenfactory.nebius.com/v1",
                    "model_id": "test/model",
                    "api_key": "offline-only",
                },
                [{"role": "user", "content": "test"}],
                [],
                max_tokens=100,
            )
        assert caught.value.category == "rate_limit"
        assert caught.value.retry_after_seconds == 123
        assert len(requests) == 1
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_restart_during_provider_cooldown_never_calls_provider(monkeypatch):
    when = datetime.now(timezone.utc) + timedelta(seconds=120)

    async def one(*args, **kwargs):
        return {
            "status": "failed",
            "result": {"category": "rate_limit", "retryNotBefore": when.isoformat()},
        }

    async def unexpected():
        pytest.fail("provider was called before its cooldown elapsed")

    monkeypatch.setattr(engine.db, "one", one)
    with pytest.raises(engine.ProviderDeferred) as caught:
        await engine.operation(
            {"id": "run", "research_mode": True}, "same-request", "model", unexpected
        )
    assert 119 <= caught.value.seconds <= 120


@pytest.mark.asyncio
async def test_research_operation_retries_recorded_daily_quota_after_cooldown(monkeypatch):
    saved = []

    async def previous(*args, **kwargs):
        return {"status": "failed", "result": {
            "category": "quota",
            "retryNotBefore": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
        }}

    async def update(*args, **kwargs):
        saved.append(args[1])
        return [args[1]]

    async def completed():
        return {"ok": True}

    monkeypatch.setattr(engine.db, "one", previous)
    monkeypatch.setattr(engine.db, "update", update)
    result = await engine.operation(
        {"id": "run", "research_mode": True}, "saved-model", "model", completed
    )
    assert result == {"ok": True}
    assert [item["status"] for item in saved] == ["started", "complete"]


@pytest.mark.asyncio
async def test_connection_qualification_preserves_signed_tool_envelope(monkeypatch):
    config = {"role": "cad", "version": 8, "model_id": "pinned/model", "context_length": 32000}
    saved, requests = [], []
    assistant = {
        "role": "assistant",
        "content": "",
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
        "tool_calls": [
            {
                "id": "call1",
                "type": "function",
                "function": {"name": "connection_check", "arguments": '{"value":"ready"}'},
                "extra_content": {"google": {"thought_signature": "opaque-signature"}},
            }
        ],
    }

    async def configuration(*args, **kwargs):
        return config

    async def candidate(*args, **kwargs):
        return {"version": 8}

    async def turn(_config, messages, tools, **kwargs):
        requests.append(deepcopy(messages))
        assert kwargs["allow_fallback"] is False
        return {
            "message": deepcopy(assistant),
            "calls": [{"id": "call1", "name": "connection_check", "input": {"value": "ready"}}],
            "provenance": {"resolvedModel": "pinned/model", "responseId": str(len(requests))},
        }

    async def update(table, body, **filters):
        saved.append((table, body, filters))
        return [body]

    monkeypatch.setattr(openrouter, "configuration", configuration)
    monkeypatch.setattr(openrouter.db, "one", candidate)
    monkeypatch.setattr(openrouter, "turn", turn)
    monkeypatch.setattr(openrouter.db, "update", update)
    assert await openrouter.test_connection("cad") == {"ok": True}
    assert len(requests) == 2
    assert requests[1][1] == assistant
    assert requests[1][2]["tool_call_id"] == "call1"
    assert saved[0][0] == "model_config_candidates" and saved[0][2]["version"] == 8
    assert saved[0][1]["qualification"]["competenceQualified"] is False


@pytest.mark.asyncio
async def test_second_tool_turn_failure_leaves_working_connection_untouched(monkeypatch):
    calls, updates = [], []

    async def configuration(*args, **kwargs):
        return {"version": 3, "model_id": "candidate", "context_length": 32000}

    async def candidate(*args, **kwargs):
        return {"version": 3}

    async def turn(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise openrouter.ModelFailure("tool_protocol", "signature lost")
        return {
            "calls": [{"id": "x", "name": "connection_check", "input": {"value": "ready"}}],
            "message": {
                "role": "assistant",
                "tool_calls": [
                    {
                        "id": "x",
                        "type": "function",
                        "function": {
                            "name": "connection_check",
                            "arguments": json.dumps({"value": "ready"}),
                        },
                    }
                ],
            },
        }

    async def update(table, body, **filters):
        updates.append((table, body))
        return [body]

    monkeypatch.setattr(openrouter, "configuration", configuration)
    monkeypatch.setattr(openrouter.db, "one", candidate)
    monkeypatch.setattr(openrouter, "turn", turn)
    monkeypatch.setattr(openrouter.db, "update", update)
    with pytest.raises(openrouter.ModelFailure):
        await openrouter.test_connection("cad")
    assert updates == [("model_config_candidates", {"active": False, "tested_at": None})]


def test_candidate_requires_pinned_model_and_known_compatible_context():
    with pytest.raises(ValueError, match="specific model"):
        validate_candidate({"provider": "openrouter", "model_id": "openrouter/free"})
    with pytest.raises(ValueError, match="context length"):
        validate_candidate(
            {
                "provider": "openai_compatible",
                "base_url": "https://api.tokenfactory.nebius.com/v1",
                "model_id": "vendor/model",
            }
        )
    validate_candidate(
        {
            "provider": "openai_compatible",
            "base_url": "https://api.tokenfactory.nebius.com/v1",
            "model_id": "vendor/model",
            "context_length": 65536,
        }
    )


def test_qualification_requires_exact_stable_resolved_identity():
    config = {"provider": "openrouter", "model_id": "vendor/pinned"}
    with pytest.raises(ValueError, match="did not identify"):
        validate_qualification(config, [{"resolvedModel": "vendor/pinned"}, {}])
    with pytest.raises(ValueError, match="different model"):
        validate_qualification(
            config, [{"resolvedModel": "vendor/pinned"}, {"resolvedModel": "vendor/substitute"}]
        )
    assert validate_qualification(config, [{"resolvedModel": "vendor/pinned"}] * 2)[
        "identityStable"
    ]


def test_qualified_connection_rejects_later_model_substitution():
    config = {"qualification": {"identityStable": True}}
    with pytest.raises(openrouter.ModelFailure) as caught:
        require_qualified_identity(config, {"provenance": {
            "requestedModel": "vendor/pinned", "resolvedModel": "vendor/other"}})
    assert caught.value.category == "model_identity"
    receipt = {"provenance": {"requestedModel": "vendor/pinned",
                              "resolvedModel": "vendor/pinned"}}
    assert require_qualified_identity(config, receipt) is receipt


def test_stream_aggregation_retains_model_and_response_identity():
    merged = openai_compatible._merge_stream(
        [
            {
                "id": "response-1",
                "model": "vendor/pinned",
                "provider": "nebius",
                "choices": [
                    {
                        "delta": {
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "type": "function",
                                    "function": {"name": "check", "arguments": "{"},
                                    "extra_content": {"signature": "opaque"},
                                }
                            ],
                        }
                    }
                ],
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [{"index": 0, "function": {"arguments": '"ok":true}'}}]
                        }
                    }
                ]
            },
        ]
    )
    assert (merged["id"], merged["model"], merged["provider"]) == (
        "response-1",
        "vendor/pinned",
        "nebius",
    )
    call = merged["choices"][0]["message"]["tool_calls"][0]
    assert call["function"]["arguments"] == '{"ok":true}'
    assert call["extra_content"] == {"signature": "opaque"}


@pytest.mark.asyncio
async def test_confirmed_context_rejection_retries_with_complete_recent_tool_pairs():
    messages = [
        {"role": "system", "content": "Current task and requirements."},
        {"role": "system", "content": "Authoritative measurements."},
        {"role": "user", "content": "Keep the original 34 mm axis spacing."},
    ]
    for index in range(12):
        call_id = f"call-{index}"
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": "diagnostic " * 150,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {"name": "read_file", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": call_id, "content": "source " * 150},
            ]
        )
    received = []

    async def request(fitted):
        received.append(fitted)
        if len(received) == 1:
            raise openrouter.ModelFailure("context_overflow", "HTTP 400", status_code=400)
        return {"message": {"role": "assistant", "content": ""}, "calls": [], "provenance": {}}

    result = await turn_with_context_recovery(
        messages,
        [],
        {"context_length": 65536, "model_id": "vendor/pinned"},
        1024,
        protocol_safe_history,
        request,
    )
    assert len(received) == 2
    assert len(received[1]) < len(received[0])
    assert received[1][:3] == messages[:3]
    assistant_ids = {call["id"] for item in received[1] for call in item.get("tool_calls", [])}
    assert all(
        item["tool_call_id"] in assistant_ids for item in received[1] if item["role"] == "tool"
    )
    assert result["provider_retry"]["contextCompactions"] == 1
    assert len(result["provenance"]["effectivePromptDigest"]) == 64


def test_input_context_rejection_does_not_lower_output_budget():
    raw = {"status": 400, "body": "maximum context length exceeded by prompt tokens"}
    assert openai_compatible._is_context_overflow(raw)
    assert not openai_compatible._is_token_limit_rejection(raw)
    assert openai_compatible._failure(400, raw["body"]).category == "context_overflow"
