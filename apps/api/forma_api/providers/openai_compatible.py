"""Transport for providers that implement the OpenAI chat-completions contract.

The graph only depends on the normalized result returned by ``turn``.  This
adapter deliberately does not send OpenRouter-only fields such as ``provider``
or ``openrouter:web_search``.
"""
import asyncio
import json
import os
import threading
import time
from urllib.parse import urlsplit

import httpx
from langsmith import traceable

from .. import db
from ..tracing import sanitize
from .openrouter import ModelFailure, _recover_text_tool_call

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MAX_OUTPUT_TOKENS = 131072
DEFAULT_REQUEST_TIMEOUT_SECONDS = 270
VERCEL_MODEL_DEADLINE_SECONDS = 270
NVIDIA_BASE_HOST = "integrate.api.nvidia.com"
NVIDIA_NEMOTRON_PREFIX = "nvidia/nemotron-3-ultra-550b-a55b"
NVIDIA_KIMI_PREFIX = "moonshotai/kimi-k3"
BASETEN_BASE_HOST = "inference.baseten.co"
BASETEN_DEEPSEEK_MODEL = "deepseek-ai/DeepSeek-V4.1-Flash"
DEEPSEEK_V4_PREFIX = "deepseek-ai/DeepSeek-V4"
NEBIUS_MINIMAX_PREFIX = "MiniMaxAI/MiniMax-M3"
NEBIUS_FIRST_FALLBACK = "MiniMaxAI/MiniMax-M3"
VERCEL_GATEWAY_HOST = "ai-gateway.vercel.sh"
NEBIUS_TOKEN_FACTORY_HOSTS = {
    "api.tokenfactory.us-central1.nebius.com",
    "api.tokenfactory.nebius.com",
}
GEMINI_API_HOST = "generativelanguage.googleapis.com"
GEMINI_DEFAULT_REQUESTS_PER_MINUTE = 4
GEMINI_503_RETRY_DELAY_SECONDS = 1.0
# A small margin makes the limit safe for any rolling 60 second window rather
# than allowing a fifth call exactly on the minute boundary.
GEMINI_REQUEST_INTERVAL_MARGIN_SECONDS = 0.1
_gemini_request_lock = threading.Lock()
_gemini_next_request_at = 0.0
VERCEL_MODEL_CHAIN = (
    "spacexai/grok-4.6",
    "tencent/hy4-preview",
    "alibaba/qwen3.8-max-0902",
)
# Testing policy for NVIDIA's hosted endpoints: Kimi is the primary model and
# Nemotron is the first fallback. The fallback can still be overridden for a
# deployment, but it must remain on the same NVIDIA OpenAI-compatible endpoint
# so the graph does not silently cross provider credentials or policies.
NVIDIA_FIRST_FALLBACK = "nvidia/nemotron-3-ultra-550b-a55b"
# A model that exceeds the provider timeout is unavailable for this step just
# like a rate-limited model; use the ordered same-endpoint fallback chain.
FALLBACK_CATEGORIES = {"overloaded", "rate_limit", "quota", "access", "timeout"}


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
        return ModelFailure("access", "The provider rejected access. Check the saved key and model access.", status_code=status)
    if status == 429:
        return ModelFailure("rate_limit", "The provider is rate-limited. Wait before continuing.", body[:8000], status_code=status)
    if status >= 500 and any(word in lower for word in ("overload", "capacity", "unavailable")):
        return ModelFailure("overloaded", "The selected provider is temporarily overloaded. Wait, then Continue.", body[:8000], status_code=status)
    return ModelFailure("provider", f"The provider rejected this request (HTTP {status}). Check model availability and tool support.", body[:8000], status_code=status)


def _output_tokens(requested: int | None = None, configured: int | None = None) -> int:
    if requested is None and configured is not None:
        requested = configured
    configured = os.getenv("OPENAI_COMPATIBLE_MAX_OUTPUT_TOKENS", str(DEFAULT_MAX_OUTPUT_TOKENS))
    try:
        limit = max(16, int(configured))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_OUTPUT_TOKENS
    return min(limit, max(16, int(requested))) if requested is not None else limit


def _request_timeout() -> httpx.Timeout:
    try:
        read_seconds = int(os.getenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "240"))
    except ValueError:
        read_seconds = 240
    return httpx.Timeout(read=max(30, min(read_seconds, 900)),
                         connect=20, write=30, pool=20)


def _request_deadline_seconds() -> float:
    """Bound total provider wall time, including a continuously active SSE stream.

    HTTPX's read timeout is an inactivity timeout: each received chunk resets it.
    A model that keeps streaming can therefore outlive the Vercel Workflow step.
    Keep room after this network deadline for parsing and checkpoint persistence.
    """
    try:
        seconds = float(os.getenv("OPENAI_COMPATIBLE_REQUEST_TIMEOUT_SECONDS", "240"))
    except (TypeError, ValueError):
        seconds = 240.0
    seconds = min(900.0, max(1.0, seconds))
    if os.getenv("VERCEL") == "1":
        seconds = min(seconds, 240.0)
    return seconds


def _is_token_limit_rejection(raw: dict) -> bool:
    if raw.get("status") not in (400, 413, 422):
        return False
    body = str(raw.get("body", "")).lower()
    return any(term in body for term in (
        "max_tokens", "max_completion_tokens", "max_output_tokens", "token limit",
        "maximum tokens", "too many tokens", "context length", "context_length",
    ))


def _lower_token_budgets(current: int):
    """Yield conservative retries only after a provider rejects the token cap."""
    for budget in (65_536, 32_768, 16_384, 8_192, 4_096, 2_048, 1_024, 512, 256, 128, 64, 16):
        if budget < current:
            yield budget


def _requests_per_minute(config: dict) -> int:
    if urlsplit(base_url(config)).hostname != GEMINI_API_HOST:
        return 0
    raw = os.getenv("GEMINI_AI_STUDIO_REQUESTS_PER_MINUTE", str(GEMINI_DEFAULT_REQUESTS_PER_MINUTE))
    try:
        return min(60, max(1, int(raw)))
    except (TypeError, ValueError):
        return GEMINI_DEFAULT_REQUESTS_PER_MINUTE


async def _pace_request(config: dict) -> None:
    """Space Gemini requests for the single-run free-tier test path.

    Google applies these quotas per project. Vercel can cold-start separate
    Python processes, so this process-local limiter is intended to pace a
    serialized Forma run; it is not a distributed quota coordinator.
    """
    rpm = _requests_per_minute(config)
    if not rpm:
        return
    global _gemini_next_request_at
    interval = 60.0 / rpm + GEMINI_REQUEST_INTERVAL_MARGIN_SECONDS
    with _gemini_request_lock:
        now = time.monotonic()
        scheduled_at = max(now, _gemini_next_request_at)
        _gemini_next_request_at = scheduled_at + interval
    wait_seconds = max(0.0, scheduled_at - time.monotonic())
    if wait_seconds:
        await asyncio.sleep(wait_seconds)


def _stream_enabled(config: dict) -> bool:
    value = config.get("stream")
    if isinstance(value, bool):
        return value
    host = urlsplit(base_url(config)).hostname
    # Baseten DeepSeek's high-reasoning turns can exceed the non-streaming read
    # timeout. Its SSE tool calls were verified against the live endpoint. A
    # per-model stream=False remains available for diagnosis; the older global
    # provider setting must not silently disable streaming for this model.
    if host == BASETEN_BASE_HOST and config.get("model_id") == BASETEN_DEEPSEEK_MODEL:
        return True
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
    model_id = str(config.get("model_id", ""))
    if host == NVIDIA_BASE_HOST and model_id.startswith(NVIDIA_KIMI_PREFIX):
        return "max"
    if host == GEMINI_API_HOST and model_id.startswith("gemini-3.7-flash"):
        return "high"
    if host in NEBIUS_TOKEN_FACTORY_HOSTS and (
        model_id.startswith(DEEPSEEK_V4_PREFIX) or model_id.startswith(NEBIUS_MINIMAX_PREFIX)
    ):
        return "high"
    if host == BASETEN_BASE_HOST and model_id == BASETEN_DEEPSEEK_MODEL:
        return "high"
    return None


def _tool_choice(config: dict, tools: list[dict]) -> str:
    if not tools:
        return "none"
    host = urlsplit(base_url(config)).hostname
    # GLM-5.3-Flash on Nebius is a forced-thinking model.  With ``auto`` it
    # can spend the entire completion budget narrating a plan for a large CAD
    # context without emitting the next action.  Requiring one tool call is
    # supported by the endpoint and keeps the graph advancing one action at a
    # time, just like the other agent providers.
    if host in NEBIUS_TOKEN_FACTORY_HOSTS:
        return "required"
    # Hosted OpenAI-compatible gateways may advertise required tool calls but
    # return an empty assistant message for large CAD prompts when
    # ``tool_choice=required`` is forced.  Auto still selects a tool when one
    # is appropriate, while allowing these models to emit a valid assistant
    # turn first.  NVIDIA's hosted agent models also require auto mode.
    return "auto"


def _sampling(config: dict) -> tuple[float, float | None]:
    host = urlsplit(base_url(config)).hostname
    model_id = str(config.get("model_id", ""))
    if host == GEMINI_API_HOST and model_id.startswith("gemini-3.7-flash"):
        # Google recommends the Gemini 3 default temperature for complex
        # reasoning; effort is separately set to high above.
        return 1.0, None
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
    timeout = _request_timeout()
    # HTTPX's read timeout only limits the pause between chunks. Enforce a
    # separate wall-clock deadline so a provider that streams indefinitely
    # cannot hold the Vercel Workflow step open until the platform retries it.
    async with asyncio.timeout(_request_deadline_seconds()):
        if not stream:
            response = await db.client().post(f"{url}/chat/completions", headers=headers,
                                              json=payload, timeout=timeout)
            return {"status": response.status_code, "body": response.text}
        events = []
        async with db.client().stream("POST", f"{url}/chat/completions", headers=headers,
                                      json=payload, timeout=timeout) as response:
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
        await _pace_request(config)
        raw = await _chat(api_key=config["api_key"], url=url, model_id=config["model_id"], messages=messages,
                          tools=tools, output_tokens=output_tokens, extra_body=extra_body,
                          tool_choice=tool_choice, stream=stream, reasoning_effort=reasoning_effort,
                          temperature=temperature, top_p=top_p,
                          langsmith_extra={"metadata": {
                              "ls_provider": config.get("provider", "openai_compatible"),
                              "ls_model_name": config["model_id"], "base_url": url,
                              "max_output_tokens": output_tokens, "stream": stream,
                              "reasoning_effort": reasoning_effort}})
        requested_output_tokens = output_tokens
        fallback_budgets = _lower_token_budgets(output_tokens) if _is_token_limit_rejection(raw) else ()
        for fallback_tokens in fallback_budgets:
            output_tokens = fallback_tokens
            extra_body = _extra_body(config, output_tokens, tools)
            await _pace_request(config)
            raw = await _chat(api_key=config["api_key"], url=url, model_id=config["model_id"], messages=messages,
                              tools=tools, output_tokens=output_tokens, extra_body=extra_body,
                              tool_choice=tool_choice, stream=stream, reasoning_effort=reasoning_effort,
                              temperature=temperature, top_p=top_p,
                              langsmith_extra={"metadata": {
                                  "ls_provider": config.get("provider", "openai_compatible"),
                                  "ls_model_name": config["model_id"], "base_url": url,
                                  "max_output_tokens": output_tokens,
                                  "requested_max_output_tokens": requested_output_tokens,
                                  "token_limit_fallback": True,
                                  "stream": stream}})
            if not _is_token_limit_rejection(raw):
                break
        if raw["status"] in (400, 422) and tools and any(term in raw["body"].lower() for term in ("tool_choice", "function calling", "unsupported")):
            # A few compatible gateways advertise tools but only accept the
            # permissive mode. A rejected 4xx request is safe to retry; the
            # response is still required to contain a valid tool call below.
            await _pace_request(config)
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
            # Gemini's OpenAI-compatible endpoint returns a provider-specific
            # thought signature on each function call.  It must be echoed in
            # the assistant tool-call message on the next request or Gemini
            # rejects the follow-up with INVALID_ARGUMENT.  Preserve the
            # opaque field while keeping the normal OpenAI tool-call shape for
            # providers that do not send it.
            tool_call = {"id": item["id"], "type": "function",
                         "function": {"name": item["function"]["name"],
                                      "arguments": json.dumps(arguments)}}
            if isinstance(item.get("extra_content"), dict):
                tool_call["extra_content"] = item["extra_content"]
            safe_message.setdefault("tool_calls", []).append(tool_call)
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


def _fallback_configs(config: dict) -> list[dict]:
    """Return the ordered fallback chain for this compatible endpoint.

    Fallbacks stay on the same endpoint and use the same encrypted provider key.
    The Vercel Gateway chain is deliberately explicit so arbitrary model IDs do
    not silently cross provider credentials or privacy policies. NVIDIA keeps
    its existing Kimi -> Nemotron behavior.
    """
    model_id = str(config.get("model_id", ""))
    host = urlsplit(base_url(config)).hostname
    if host == NVIDIA_BASE_HOST and model_id.startswith(NVIDIA_KIMI_PREFIX):
        fallback_ids = [os.getenv("OPENAI_COMPATIBLE_FALLBACK_MODEL_ID", NVIDIA_FIRST_FALLBACK).strip()]
    elif host == VERCEL_GATEWAY_HOST and model_id in VERCEL_MODEL_CHAIN:
        fallback_ids = list(VERCEL_MODEL_CHAIN[VERCEL_MODEL_CHAIN.index(model_id) + 1:])
    elif host in NEBIUS_TOKEN_FACTORY_HOSTS and model_id.startswith(DEEPSEEK_V4_PREFIX):
        fallback_ids = [os.getenv("OPENAI_COMPATIBLE_FALLBACK_MODEL_ID", NEBIUS_FIRST_FALLBACK).strip()]
    else:
        fallback_ids = []
    fallback_stream = True if host == NVIDIA_BASE_HOST else config.get("stream", False)
    return [
        # Preserve the configured transport mode.  In particular, a Nebius
        # retry should not be changed to streaming when the primary is
        # non-streaming: waiting for a complete tool payload is more reliable
        # for long reasoning responses and avoids an open stream with no
        # actionable tool call.
        {**config, "model_id": fallback_id, "stream": fallback_stream, "fallback_for": model_id}
        for fallback_id in fallback_ids
        if fallback_id and fallback_id != model_id
    ]


# Kept as a small compatibility helper for callers that only need one candidate.
def _fallback_config(config: dict) -> dict | None:
    return next(iter(_fallback_configs(config)), None)


def retry_config(config: dict) -> dict:
    """Select the first same-endpoint fallback for an explicit user retry.

    A provider timeout can consume almost the complete Vercel invocation
    window.  Retrying the primary inside the next invocation would repeat the
    same failure before the fallback gets any time.  The graph calls this only
    after the user has explicitly resumed an uncertain operation.
    """
    return _fallback_config(config) or config


def _gemini_key_fallbacks(config: dict) -> list[str]:
    """Return saved secondary keys only for the Google AI Studio endpoint."""
    if not _is_gemini_config(config):
        return []
    values = config.get("api_key_fallbacks") or []
    if not isinstance(values, list):
        return []
    primary = config.get("api_key")
    return [value for value in values if isinstance(value, str) and value and value != primary][:2]


def _is_gemini_config(config: dict) -> bool:
    try:
        return urlsplit(base_url(config)).hostname == GEMINI_API_HOST
    except ModelFailure:
        return False


async def turn(config: dict, messages: list[dict], tools: list[dict], *, max_tokens: int | None = None,
               web_search=False, max_searches=0, allow_fallback=True, allow_key_fallback=True):
    try:
        return await _turn_once(config, messages, tools, max_tokens=max_tokens,
                                web_search=web_search, max_searches=max_searches)
    except ModelFailure as primary_error:
        same_key_retry = False
        # Google distinguishes 503 service overload from 429 quota errors.
        # Retry a 503 once with the same key and bounded backoff; credential
        # rotation below remains restricted to actual HTTP 429 responses.
        if (_is_gemini_config(config) and primary_error.status_code == 503
                and primary_error.category == "overloaded"):
            same_key_retry = True
            await asyncio.sleep(GEMINI_503_RETRY_DELAY_SECONDS)
            try:
                result = await _turn_once(config, messages, tools, max_tokens=max_tokens,
                                          web_search=web_search, max_searches=max_searches)
            except ModelFailure as retry_error:
                primary_error = retry_error
            else:
                result["provider_retry"] = {"attempts": 1, "reason": "http_503_same_key"}
                return result
        # A completed HTTP 429 or 503 is safe to retry with the explicitly
        # saved Google key order. The same-key 503 retry above gets first
        # chance; rotating afterward also covers key/project-scoped capacity
        # failures without interrupting the LangGraph run. Never rotate on
        # timeouts, whose provider-side outcome is ambiguous.
        retryable_google_error = (
            (primary_error.status_code == 429 and primary_error.category == "rate_limit")
            or (primary_error.status_code == 503 and primary_error.category == "overloaded")
        )
        if allow_key_fallback and retryable_google_error:
            fallback_keys = _gemini_key_fallbacks(config)
            last_key_error = primary_error
            for key_index, api_key in enumerate(fallback_keys, start=2):
                try:
                    result = await _turn_once({**config, "api_key": api_key}, messages, tools,
                                              max_tokens=max_tokens, web_search=web_search,
                                              max_searches=max_searches)
                except ModelFailure as key_error:
                    if not ((key_error.status_code == 429 and key_error.category == "rate_limit")
                            or (key_error.status_code == 503 and key_error.category == "overloaded")):
                        raise
                    last_key_error = key_error
                    continue
                result["credential_fallback"] = {
                    "key_index": key_index,
                    "reason": f"http_{primary_error.status_code}",
                }
                if same_key_retry:
                    result["provider_retry"] = {"attempts": 1, "reason": "http_503_same_key"}
                return result
            primary_error = last_key_error
        # A timeout is an ambiguous external operation. Do not start a second
        # long model request in the same 300-second Vercel invocation; the
        # operation ledger records it for an explicit Continue, which switches
        # to the configured same-endpoint fallback in the next invocation.
        if primary_error.category == "timeout" and os.getenv("VERCEL") == "1":
            raise
        fallback_chain = _fallback_configs(config) if allow_fallback else []
        if not fallback_chain or primary_error.category not in FALLBACK_CATEGORIES:
            raise
        last_error = primary_error
        for fallback in fallback_chain:
            try:
                result = await _turn_once(fallback, messages, tools, max_tokens=max_tokens,
                                          web_search=web_search, max_searches=max_searches)
            except ModelFailure as fallback_error:
                last_error = fallback_error
                if fallback_error.category not in FALLBACK_CATEGORIES:
                    break
                continue
            result["fallback"] = {"from": config.get("model_id"), "to": fallback["model_id"],
                                   "reason": primary_error.category}
            return result
        raise ModelFailure(last_error.category,
            f"The selected model and its configured fallbacks failed. {last_error}",
            last_error.diagnostic) from None


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
