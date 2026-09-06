"""Transport for providers that implement the OpenAI chat-completions contract.

The graph only depends on the normalized result returned by ``turn``.  This
adapter deliberately does not send OpenRouter-only fields such as ``provider``
or ``openrouter:web_search``.
"""
import json
import os
from urllib.parse import urlsplit

import httpx
from langsmith import traceable

from .. import db
from ..tracing import sanitize
from .openrouter import ModelFailure, _recover_text_tool_call

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_OUTPUT_TOKENS = 32768
NVIDIA_BASE_HOST = "integrate.api.nvidia.com"
NVIDIA_NEMOTRON_PREFIX = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_KIMI_PREFIX = "moonshotai/kimi-k3"
# Testing policy for NVIDIA's hosted endpoints: Kimi is the primary model and
# Nemotron is the first fallback. The fallback can still be overridden for a
# deployment, but it must remain on the same NVIDIA OpenAI-compatible endpoint
# so the graph does not silently cross provider credentials or policies.
NVIDIA_FIRST_FALLBACK = "nvidia/nemotron-3-ultra-550b-a55b"
FALLBACK_CATEGORIES = {"overloaded", "rate_limit", "quota", "access"}


def base_url(config: dict) -> str:
    value = (config.get("base_url") or os.getenv("OPENAI_COMPATIBLE_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
    parsed = urlsplit(value)
    local = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme != "https" and not local:
        raise ModelFailure("configuration", "An external provider base URL must use HTTPS.")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ModelFailure("configuration", "The provider base URL cannot contain credentials or query parameters.")
    if not parsed.netloc:
        raise ModelFailure("configuration", "Enter a complete provider base URL, such as https://api.example.com/v1.")
    return value


def _failure(status: int, body: str) -> ModelFailure:
    lower = body.lower()
    if status in (401, 403):
        return ModelFailure("access", "The provider rejected access. Check the saved key and model access.")
    if status == 429:
        return ModelFailure("rate_limit", "The provider is rate-limited. Wait before continuing.", body[:8000])
    if status >= 500 and any(word in lower for word in ("overload", "capacity", "unavailable")):
        return ModelFailure("overloaded", "The selected provider is temporarily overloaded. Wait, then Continue.", body[:8000])
    return ModelFailure("provider", f"The provider rejected this request (HTTP {status}). Check model availability and tool support.", body[:8000])


def _output_tokens(requested: int | None = None, configured: int | None = None) -> int:
    if requested is None and configured is not None:
        requested = configured
    configured = os.getenv("OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
    try:
        limit = max(16, int(configured))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_OUTPUT_TOKENS
    return min(limit, max(16, int(requested))) if requested is not None else limit


def _stream_enabled(config: dict) -> bool:
    value = config.get("stream")
    if isinstance(value, bool):
        return value
    host = urlsplit(base_url(config)).hostname
    default = "true" if host == NVIDIA_BASE_HOST else "false"
    return os.getenv("OPENAI_COMPATIBLE_STREAM", default).lower() == "true"


def _extra_body(config: dict, output_tokens: int, tools: list[dict]) -> dict:
    """Return optional provider-specific body fields without exposing secrets.

    NVIDIA documents model-specific reasoning controls for its hosted DeepSeek,
    Kimi-K3, and Nemotron endpoints. An environment JSON override keeps the
    adapter usable for other OpenAI-compatible endpoints without adding
    provider branches to graph code.
    """
    raw = os.getenv("OPENAI_COMPATIBLE_EXTRA_BODY_JSON", "")
    value = {}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                value = parsed
        except (TypeError, ValueError):
            pass
    host = urlsplit(base_url(config)).hostname
    model_id = str(config.get("model_id", ""))
    if host == NVIDIA_BASE_HOST and model_id.startswith("deepseek-ai/deepseek-v4"):
        value.setdefault("chat_template_kwargs", {}).setdefault("thinking", False)
    if host == NVIDIA_BASE_HOST and model_id.startswith(NVIDIA_NEMOTRON_PREFIX):
        template = value.setdefault("chat_template_kwargs", {})
        template.setdefault("enable_thinking", True)
        if tools:
            # NVIDIA documents this flag for coding agents so reasoning and
            # tool-call parsers both receive a non-empty assistant message.
            template.setdefault("force_nonempty_content", True)
        value.setdefault("reasoning_budget", min(16_384, max(1_024, output_tokens // 2)))
    return value


def _reasoning_effort(config: dict) -> str | None:
    host = urlsplit(base_url(config)).hostname
    if host == NVIDIA_BASE_HOST and str(config.get("model_id", "")).startswith(NVIDIA_KIMI_PREFIX):
        return "max"
    return None


def _tool_choice(config: dict, tools: list[dict]) -> str:
    if not tools:
        return "none"
    host = urlsplit(base_url(config)).hostname
    # Hosted OpenAI-compatible gateways may advertise required tool calls but
    # return an empty assistant message for large CAD prompts when
    # ``tool_choice=required`` is forced.  Auto still selects a tool when one
    # is appropriate, while allowing these models to emit a valid assistant
    # turn first.  NVIDIA's hosted agent models also require auto mode.
    return "auto"


def _sampling(config: dict) -> tuple[float, float | None]:
    host = urlsplit(base_url(config)).hostname
    model_id = str(config.get("model_id", ""))
    if host == NVIDIA_BASE_HOST and (model_id.startswith(NVIDIA_KIMI_PREFIX) or model_id.startswith(NVIDIA_NEMOTRON_PREFIX)):
        return 1.0, 0.95
    return 0.2, None


def _trace_inputs(inputs: dict) -> dict:
    return sanitize({k: v for k, v in inputs.items() if k != "api_key"})


@traceable(name="OpenAI-compatible chat", run_type="llm", process_inputs=_trace_inputs)
async def _chat(*, api_key: str, url: str, model_id: str, messages: list[dict], tools: list[dict], output_tokens: int,
                extra_body: dict, tool_choice: str, stream: bool, reasoning_effort: str | None,
                temperature: float, top_p: float | None):
    payload = {"model": model_id, "messages": messages, "temperature": temperature,
               "max_tokens": output_tokens, "stream": stream}
    if top_p is not None:
        payload["top_p"] = top_p
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    if reasoning_effort:
        payload["reasoning_effort"] = reasoning_effort
    if stream:
        payload["stream_options"] = {"include_usage": True}
    # ``extra_body`` is an OpenAI SDK convenience: its keys are merged into
    # the JSON request, rather than sent under a literal ``extra_body`` key.
    if extra_body:
        payload.update(extra_body)
    headers = {"Authorization": f"Bearer {api_key}",
               "Accept": "text/event-stream" if stream else "application/json"}
    if not stream:
        response = await db.client().post(f"{url}/chat/completions", headers=headers,
                                          json=payload, timeout=240)
        return {"status": response.status_code, "body": response.text}
    events = []
    async with db.client().stream("POST", f"{url}/chat/completions", headers=headers,
                                  json=payload, timeout=240) as response:
        if not response.is_success:
            return {"status": response.status_code,
                    "body": (await response.aread()).decode("utf-8", errors="replace")}
        async for line in response.aiter_lines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            try:
                events.append(json.loads(data))
            except ValueError:
                continue
    for event in events:
        # Some hosted gateways encode provider failures as an SSE data event
        # while keeping the HTTP status at 200, followed by ``[DONE]``.
        if isinstance(event.get("error"), dict):
            error = event["error"]
            return {"status": int(error.get("code", 500)), "body": json.dumps({"error": error})}
    return {"status": 200, "body": json.dumps(_merge_stream(events))}


def _merge_stream(events: list[dict]) -> dict:
    """Reconstruct one OpenAI chat response from SSE deltas."""
    content, reasoning, tool_calls = [], [], {}
    role = "assistant"
    usage = {}
    for event in events:
        usage = event.get("usage") or usage
        choices = event.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta") or choice.get("message") or {}
        role = delta.get("role") or role
        if delta.get("content") is not None:
            content.append(str(delta["content"]))
        if delta.get("reasoning_content") is not None:
            reasoning.append(str(delta["reasoning_content"]))
        for item in delta.get("tool_calls") or []:
            index = item.get("index", len(tool_calls))
            target = tool_calls.setdefault(index, {"id": "", "type": "function",
                "function": {"name": "", "arguments": ""}})
            target["id"] += item.get("id") or ""
            target["type"] = item.get("type") or target["type"]
            function = item.get("function") or {}
            target["function"]["name"] += function.get("name") or ""
            target["function"]["arguments"] += function.get("arguments") or ""
    message = {"role": role, "content": "".join(content)}
    if reasoning:
        message["reasoning_content"] = "".join(reasoning)
    if tool_calls:
        message["tool_calls"] = list(tool_calls.values())
    return {"choices": [{"message": message}], "usage": usage}


async def _turn_once(config: dict, messages: list[dict], tools: list[dict], *, max_tokens: int | None = None,
                     web_search=False, max_searches=0):
    if web_search and max_searches > 0:
        raise ModelFailure("capability", "This OpenAI-compatible provider does not advertise Forma web search. Use OpenRouter for engineering web research.")
    url = base_url(config)
    output_tokens = _output_tokens(max_tokens, config.get("max_output_tokens"))
    tool_choice = _tool_choice(config, tools)
    stream = _stream_enabled(config)
    extra_body = _extra_body(config, output_tokens, tools)
    reasoning_effort = _reasoning_effort(config)
    temperature, top_p = _sampling(config)
    try:
        raw = await _chat(api_key=config["api_key"], url=url, model_id=config["model_id"], messages=messages,
                          tools=tools, output_tokens=output_tokens, extra_body=extra_body,
                          tool_choice=tool_choice, stream=stream, reasoning_effort=reasoning_effort,
                          temperature=temperature, top_p=top_p,
                          langsmith_extra={"metadata": {
                              "ls_provider": config.get("provider", "openai_compatible"),
                              "ls_model_name": config["model_id"], "base_url": url,
                              "max_output_tokens": output_tokens, "stream": stream,
                              "reasoning_effort": reasoning_effort}})
        if raw["status"] in (400, 422) and output_tokens > 16_384 and any(term in raw["body"].lower() for term in ("max_tokens", "token limit", "maximum")):
            # NVIDIA's examples use 16,384 for Nemotron. Keep a larger Forma
            # budget for providers that support it, then retry a rejected
            # request at the documented compatibility floor.
            output_tokens = 16_384
            extra_body = _extra_body(config, output_tokens, tools)
            raw = await _chat(api_key=config["api_key"], url=url, model_id=config["model_id"], messages=messages,
                              tools=tools, output_tokens=output_tokens, extra_body=extra_body,
                              tool_choice=tool_choice, stream=stream, reasoning_effort=reasoning_effort,
                              temperature=temperature, top_p=top_p,
                              langsmith_extra={"metadata": {
                                  "ls_provider": config.get("provider", "openai_compatible"),
                                  "ls_model_name": config["model_id"], "base_url": url,
                                  "max_output_tokens": output_tokens, "token_limit_fallback": True,
                                  "stream": stream}})
        if raw["status"] in (400, 422) and tools and any(term in raw["body"].lower() for term in ("tool_choice", "function calling", "unsupported")):
            # A few compatible gateways advertise tools but only accept the
            # permissive mode. A rejected 4xx request is safe to retry; the
            # response is still required to contain a valid tool call below.
            raw = await _chat(api_key=config["api_key"], url=url, model_id=config["model_id"], messages=messages,
                              tools=tools, output_tokens=output_tokens, extra_body=extra_body,
                              tool_choice="auto", stream=stream, reasoning_effort=reasoning_effort,
                              temperature=temperature, top_p=top_p,
                              langsmith_extra={"metadata": {
                                  "ls_provider": config.get("provider", "openai_compatible"),
                                  "ls_model_name": config["model_id"], "base_url": url,
                                  "max_output_tokens": output_tokens, "tool_choice_fallback": True,
                                  "stream": stream}})
    except (httpx.TimeoutException, TimeoutError):
        raise ModelFailure("timeout", "The model exceeded its response timeout. Continue only when ready to retry.") from None
    except httpx.HTTPError:
        raise ModelFailure("connection", "The model connection was interrupted. Continue deliberately to retry.") from None
    if raw["status"] < 200 or raw["status"] >= 300:
        raise _failure(raw["status"], raw["body"])
    try:
        body = json.loads(raw["body"])
        if body.get("error"):
            error = body["error"]
            raise _failure(int(error.get("code", 500)), str(error.get("message", "")))
        message = body["choices"][0]["message"]
        safe_message = {"role": "assistant", "content": message.get("content") or ""}
        if message.get("reasoning_content"):
            # Kimi-K3 requires the complete reasoning history on follow-up
            # tool turns; it is retained in the private checkpoint only.
            safe_message["reasoning_content"] = message["reasoning_content"]
        calls = []
        for item in message.get("tool_calls") or []:
            arguments = item["function"].get("arguments", {})
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            safe_message.setdefault("tool_calls", []).append({"id": item["id"], "type": "function",
                "function": {"name": item["function"]["name"], "arguments": json.dumps(arguments)}})
            calls.append({"id": item["id"], "name": item["function"]["name"], "input": arguments})
        if not calls:
            recovered = _recover_text_tool_call(message.get("content"), tools)
            if recovered:
                safe_message.setdefault("tool_calls", []).append(recovered["tool_call"])
                calls.append({"id": recovered["id"], "name": recovered["name"],
                              "input": recovered["input"]})
        usage = body.get("usage") or {}
        return {"message": safe_message, "calls": calls,
                "inputTokens": usage.get("prompt_tokens", 0), "outputTokens": usage.get("completion_tokens", 0),
                "cost": usage.get("cost"), "webSearchRequests": 0}
    except ModelFailure:
        raise
    except (KeyError, IndexError, ValueError, TypeError):
        raise ModelFailure("tool_protocol", "The model returned an invalid tool action. No action was executed.") from None


def _fallback_config(config: dict) -> dict | None:
    """Return the configured first fallback without crossing provider boundaries.

    NVIDIA's hosted Kimi and Nemotron endpoints use the same OpenAI-compatible
    contract and key. Kimi is the primary testing model; only a Kimi failure
    can select the first fallback, so fallback handling cannot recurse.
    """
    model_id = str(config.get("model_id", ""))
    if not model_id.startswith(NVIDIA_KIMI_PREFIX):
        return None
    if urlsplit(base_url(config)).hostname != NVIDIA_BASE_HOST:
        return None
    fallback_id = os.getenv("OPENAI_COMPATIBLE_FALLBACK_MODEL_ID", NVIDIA_FIRST_FALLBACK).strip()
    if not fallback_id or fallback_id == model_id:
        return None
    return {**config, "model_id": fallback_id, "stream": True, "fallback_for": model_id}


async def turn(config: dict, messages: list[dict], tools: list[dict], *, max_tokens: int | None = None,
               web_search=False, max_searches=0):
    try:
        return await _turn_once(config, messages, tools, max_tokens=max_tokens,
                                web_search=web_search, max_searches=max_searches)
    except ModelFailure as primary_error:
        fallback = _fallback_config(config)
        if not fallback or primary_error.category not in FALLBACK_CATEGORIES:
            raise
        try:
            result = await _turn_once(fallback, messages, tools, max_tokens=max_tokens,
                                      web_search=web_search, max_searches=max_searches)
        except ModelFailure as fallback_error:
            raise ModelFailure(fallback_error.category,
                f"The primary NVIDIA Kimi-K3 model and its Nemotron fallback failed. {fallback_error}",
                fallback_error.diagnostic) from None
        result["fallback"] = {"from": config.get("model_id"), "to": fallback["model_id"],
                               "reason": primary_error.category}
        return result


async def catalog(config: dict, refresh=False) -> list[dict]:
    """Best-effort model catalog for a compatible provider."""
    url = base_url(config)
    try:
        response = await db.client().get(f"{url}/models", headers={"Authorization": f"Bearer {config['api_key']}"}, timeout=10)
    except (httpx.TimeoutException, httpx.HTTPError):
        return []
    if not response.is_success:
        return []
    data = response.json().get("data", [])
    return [{"id": item.get("id"), "name": item.get("id"), "context_length": 0}
            for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
