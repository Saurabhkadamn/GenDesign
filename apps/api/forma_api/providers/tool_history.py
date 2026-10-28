"""Prepare saved tool calls for OpenAI-compatible provider replay."""

from copy import deepcopy
import json


def _has_signature(value: object) -> bool:
    if isinstance(value, dict):
        return any(
            "signature" in str(key).lower() or _has_signature(item)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(_has_signature(item) for item in value)
    return False


def normalized_tool_history(messages: list[dict]) -> list[dict]:
    """Replay the parsed arguments that the tool actually received.

    Some model endpoints emit JSON with repeated object keys. Python accepts
    that response and the tool executes the final-key interpretation, but a
    different upstream parser can reject the original string on the next turn.
    Re-encoding the parsed object keeps replay aligned with the executed call.
    Opaque reasoning and signature fields are retained unchanged.
    """
    result = deepcopy(messages)
    for message in result:
        if message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function")
            if not isinstance(function, dict):
                raise ValueError("Saved tool call has no function object")
            arguments = function.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError as exc:
                    raise ValueError("Saved tool call arguments are invalid JSON") from exc
            if not isinstance(arguments, dict):
                raise ValueError("Saved tool call arguments must be a JSON object")
            canonical = json.dumps(
                arguments, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            if function.get("arguments") != canonical and _has_signature(call.get("extra_content")):
                raise ValueError("Saved signed tool call cannot be re-encoded safely")
            function["arguments"] = canonical
    return result
