"""OpenRouter transport: explicit model selection, no paid or provider fallback."""
import json
import asyncio
import math
import time

import httpx
from fastapi import HTTPException
from langsmith import traceable

from .. import db
from ..config import settings
from ..security import decrypt_secret

_catalog: tuple[float, list] = (0, [])
NEMOTRON = "nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_MAX_COMPLETION_TOKENS = 32768


class ModelFailure(Exception):
    def __init__(self, category: str, message: str, diagnostic: str = ""):
        self.category = category
        self.diagnostic = diagnostic
        super().__init__(message)


def select_config(rows: list[dict], role: str, testing=False):
    selected = next((x for x in rows if x["role"] == role), None)
    if testing:
        return selected
    if selected and selected["active"] and selected["tested_at"]:
        return selected
    default = next((x for x in rows if x["role"] == "coordinator"), None)
    return default if default and default["active"] and default["tested_at"] else None


async def configuration(role: str, testing=False):
    row = select_config(await db.rest("model_configs", params={"role": f"in.({role},coordinator)"}), role, testing)
    if not row:
        raise ModelFailure("configuration", f"No usable {role} connection. Test and activate the default or this specialist in Settings, then Continue.")
    return {**row, "api_key": decrypt_secret(row["encrypted_key"], row["role"])}


async def catalog(refresh=False):
    global _catalog
    if not refresh and _catalog[0] > time.monotonic():
        return _catalog[1]
    response = await db.client().get("https://openrouter.ai/api/v1/models", timeout=10)
    if not response.is_success:
        raise ModelFailure("catalog", "The model catalog is unavailable. No model request was sent.")
    rows = [m for m in response.json().get("data", []) if isinstance(m.get("id"), str) and isinstance(m.get("pricing"), dict)]
    _catalog = (time.monotonic() + 60, rows)
    return rows


def free_tool_model(model):
    try:
        pricing = model["pricing"]
        return (model["id"].endswith(":free") and "tools" in model.get("supported_parameters", [])
                and {"prompt", "completion"} <= pricing.keys()
                and all(str(x).strip() and math.isfinite(float(x)) and float(x) == 0 for x in pricing.values()))
    except (ValueError, TypeError, KeyError):
        return False


def completion_settings(model: dict | None, requested: int | None = None) -> tuple[str, int]:
    """Choose the advertised output parameter and the model/provider maximum."""
    supported = set((model or {}).get("supported_parameters") or [])
    raw_limit = ((model or {}).get("top_provider") or {}).get("max_completion_tokens")
    try:
        advertised = int(raw_limit) if raw_limit is not None else 0
    except (TypeError, ValueError):
        advertised = 0
    if advertised <= 0:
        advertised = DEFAULT_MAX_COMPLETION_TOKENS
    limit = advertised if requested is None else min(advertised, max(16, int(requested)))
    parameter = "max_completion_tokens" if "max_completion_tokens" in supported else "max_tokens"
    return parameter, limit


def failure(status: int, body: str) -> ModelFailure:
    if status >= 500 and any(word in body.lower() for word in ("overload", "capacity", "unavailable")):
        return ModelFailure("overloaded", "The selected model's provider is temporarily overloaded. Wait, then Continue, or select another connection in Settings.", body[:8000])
    if status in (401, 403):
        return ModelFailure("access", "The provider rejected access. Check the saved key and access to this model.")
    if status == 429:
        daily = any(x in body.lower() for x in ("daily", "per-day", "per day"))
        return ModelFailure("quota" if daily else "rate_limit", "The free-model daily quota is exhausted. Wait for reset before continuing." if daily else "The model is rate-limited. Wait before continuing.")
    if status == 404 and any(x in body.lower() for x in ("data policy", "data collection", "privacy")):
        return ModelFailure("privacy", "No endpoint meets the configured data-collection policy. Choose a compatible model.")
    return ModelFailure("provider", f"The provider rejected this request (HTTP {status}). Check model availability and tool support.", body[:8000])


def _trace_inputs(inputs: dict) -> dict:
    from ..tracing import sanitize
    return sanitize({k: v for k, v in inputs.items() if k != "api_key"})


@traceable(name="OpenRouter chat", run_type="llm", process_inputs=_trace_inputs)
async def _openrouter_chat(*, api_key: str, model_id: str, messages: list[dict], tools: list[dict],
                           token_parameter: str, output_tokens: int, provider: dict,
                           web_search: bool, max_searches: int):
    request_tools = list(tools)
    if web_search and max_searches > 0:
        request_tools.append({"type": "openrouter:web_search", "parameters": {
            "max_total_results": min(3, max_searches * 3), "search_context_size": "low"}})
    payload = {"model": model_id, "messages": messages, "tools": request_tools,
        "tool_choice": "required" if tools else "none", "provider": provider,
        token_parameter: output_tokens}
    response = await db.client().post("https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json=payload, timeout=240)
    return {"status": response.status_code, "body": response.text}


async def turn(config: dict, messages: list[dict], tools: list[dict], *, max_tokens: int | None = None,
               web_search=False, max_searches=0):
    cfg = settings()
    model = None
    try:
        model = next((m for m in await catalog() if m["id"] == config["model_id"]), None)
    except ModelFailure:
        if cfg.free_only:
            raise
    if cfg.free_only:
        if not model or not free_tool_model(model):
            raise ModelFailure("free_only", "Testing is restricted to listed zero-price models with tool support.")
    token_parameter, output_tokens = completion_settings(model, max_tokens)
    policy = {"allow_fallbacks": False, "require_parameters": True,
              "data_collection": "allow" if cfg.nemotron_testing and config["model_id"] == NEMOTRON else "deny"}
    if cfg.free_only:
        policy["max_price"] = {k: 0 for k in ("prompt", "completion", "request", "image", "audio")}
    try:
        # Large CAD tool calls may legitimately take longer than a short chat
        # response. Keep the deadline below the hosted workflow-step ceiling.
        async with asyncio.timeout(270):
            raw = await _openrouter_chat(api_key=config["api_key"], model_id=config["model_id"],
                messages=messages, tools=tools, token_parameter=token_parameter,
                output_tokens=output_tokens, provider=policy,
                web_search=web_search, max_searches=max_searches,
                langsmith_extra={"metadata": {"ls_provider": "openrouter",
                    "ls_model_name": config["model_id"],
                    "max_output_tokens": output_tokens,
                    "output_token_parameter": token_parameter}})
    except (httpx.TimeoutException, TimeoutError):
        raise ModelFailure("timeout", "The model exceeded its response timeout. Its outcome is uncertain; Continue only when ready to retry.") from None
    except httpx.HTTPError:
        raise ModelFailure("connection", "The model connection was interrupted. Continue deliberately to retry.") from None
    if raw["status"] < 200 or raw["status"] >= 300:
        raise failure(raw["status"], raw["body"])
    body = json.loads(raw["body"])
    if body.get("error"):
        error = body["error"]
        raise failure(int(error.get("code", 500)), str(error.get("message", "")))
    try:
        message = body["choices"][0]["message"]
        # Preserve tool protocol, but do not retain provider headers, credentials or reasoning fields.
        safe_message = {"role": "assistant", "content": message.get("content") or ""}
        calls = []
        if message.get("tool_calls"):
            safe_message["tool_calls"] = message["tool_calls"]
            for item in message["tool_calls"]:
                calls.append({"id": item["id"], "name": item["function"]["name"],
                              "input": json.loads(item["function"]["arguments"])})
        usage = body.get("usage", {})
        return {"message": safe_message, "calls": calls, "inputTokens": usage.get("prompt_tokens", 0),
                "outputTokens": usage.get("completion_tokens", 0), "cost": usage.get("cost"),
                "webSearchRequests": (usage.get("server_tool_use") or {}).get("web_search_requests", 0)}
    except (KeyError, IndexError, ValueError, TypeError):
        raise ModelFailure("tool_protocol", "The model returned an invalid tool action. No action was executed.") from None


async def test_connection(role: str):
    config = await configuration(role, testing=True)
    test_tool = [{"type": "function", "function": {"name": "connection_check", "description": "Check tool calling",
                  "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["ready"]}},
                                 "required": ["value"], "additionalProperties": False}}}]
    try:
        result = await turn(config, [{"role": "user", "content": "Call connection_check with value ready. Do not answer with text."}], test_tool, max_tokens=2048)
        if not any(c["name"] == "connection_check" and c["input"] == {"value": "ready"} for c in result["calls"]):
            raise ModelFailure("tool_protocol", "The model did not produce a valid connection-check tool call.")
    except ModelFailure:
        await db.update("model_configs", {"active": False, "tested_at": None}, role=role, version=config["version"])
        raise
    from datetime import datetime, timezone
    rows = await db.update("model_configs", {"tested_at": datetime.now(timezone.utc).isoformat()}, role=role, version=config["version"])
    if not rows:
        raise HTTPException(409, "The configuration changed. Test the current version.")
    return {"ok": True}
