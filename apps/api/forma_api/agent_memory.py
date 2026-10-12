"""Provider-neutral working memory. Facts retain provenance; notes are not validation."""
import json
from copy import deepcopy


def user_update(state: dict, message: str, kind: str = "continuation") -> dict:
    message = str(message or "").strip()
    if not message:
        return {}
    entries = deepcopy(state.get("user_updates") or [])
    entry = {"kind": kind, "message": message}
    if entries and entries[-1] == entry:
        return {}
    entries.append(entry)
    request = state.get("clarified_request") or state.get("original_request", "")
    label = "clarification" if kind == "clarification" else "continuation"
    return {"user_updates": entries,
            "clarified_request": request + "\n\nUser " + label + ": " + message}


def record_observation(state: dict, name: str, result: dict, *, candidate: str = "") -> dict:
    """Retain outcomes outside rolling messages, without duplicating source or reasoning."""
    memory = deepcopy(state.get("working_memory") or {})
    outcomes = memory.setdefault("outcomes", {})
    error = result.get("error") or {}
    if not isinstance(error, dict):
        error = {"diagnostic": str(error)}
    location = error.get("location") or result.get("location") or {}
    if not isinstance(location, dict):
        location = {}
    key = error.get("fingerprint") or result.get("category") or name
    if location.get("file"):
        key = str(key) + ":" + location["file"]
    if not candidate and str(key) in outcomes:
        return memory  # A historical tool message cannot erase known provenance.
    fact = {"tool": name, "candidateHash": candidate,
            "ok": result.get("ok"), "category": error.get("category") or result.get("category"),
            "location": location,
            "guidance": error.get("guidance") or result.get("repairGuidance") or result.get("message"),
            "diagnostic": (error.get("diagnostic") or "")[-3000:]}
    if result.get("workspaceStatus"):
        fact["workspaceStatus"] = result["workspaceStatus"]
    if result.get("issues"):
        fact["issues"] = result["issues"][:40]
    if name in {"apply_changes", "update_parameters"} and result.get("ok"):
        changed = memory.setdefault("changedFiles", {})
        for path in result.get("changedFiles") or []:
            changed[path] = {"candidateHash": candidate, "status": "edited_not_verified"}
        fact["changedFiles"] = result.get("changedFiles") or []
    # Latest fact for the same failure/location replaces repeated identical noise.
    outcomes.pop(str(key), None)
    outcomes[str(key)] = fact
    return memory


def memory_context(state: dict, candidate: str = "") -> dict:
    memory = state.get("working_memory") or {}
    facts = list((memory.get("outcomes") or {}).values())
    return {"userUpdates": state.get("user_updates") or [],
            "notes": memory.get("notes") or {},
            "notesAreAgentReported": True,
            "changedFiles": memory.get("changedFiles") or {},
            "recentOutcomes": [{**fact, "currentCandidate": bool(candidate) and fact.get("candidateHash") == candidate}
                               for fact in facts[-12:]],
            "outcomeCount": len(facts),
            "lastValidatedMilestone": state.get("last_milestone_hash") or None,
            "instruction": "Notes and old outcomes are working memory, not current validation. Read source and test changed geometry. All parts remain editable."}


def estimate_tokens(value) -> int:
    """Conservative UTF-8 estimate for providers without an available tokenizer."""
    return (len(json.dumps(value, ensure_ascii=False).encode("utf-8")) + 2) // 3 + 16


def fit_dialogue(messages: list[dict], tools: list[dict], budget: int, safe_history) -> list[dict]:
    """Drop oldest complete dialogue groups; never truncate authoritative context."""
    prefix, history = messages[:2], list(messages[2:])
    first = history[:1]
    tail = history[1:]
    while tail and estimate_tokens([*prefix, *first, *tail, tools]) > budget:
        tail.pop(0)
        tail = safe_history(tail)
    result = [*prefix, *first, *tail]
    if estimate_tokens([*result, tools]) > budget:
        # The original request is also supplied in the authoritative context.
        result = prefix
    if estimate_tokens([*result, tools]) > budget:
        raise ValueError("The authoritative request/workspace exceeds the context budget. Split the task or raise its configured input budget; no requirements were truncated.")
    return result


def compact_narrative(message: dict) -> dict:
    """A huge response without an action is not useful working state."""
    result = deepcopy(message)
    content = result.get("content") or ""
    if not result.get("tool_calls") and len(content) > 8000:
        result["content"] = ("The previous response contained no executable tool action. "
                             "Its full output is retained in the generation record. No workspace change occurred. "
                             "Choose the next action using the request, source and working memory.")
    return result


def capture_history(state: dict, history: list[dict]) -> dict:
    memory = deepcopy(state.get("working_memory") or {})
    calls = {}
    for message in history:
        for call in message.get("tool_calls") or []:
            calls[call.get("id")] = call.get("function", {}).get("name")
        if message.get("role") != "tool":
            continue
        name = calls.get(message.get("tool_call_id"))
        if name:
            try:
                result = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                continue
            if isinstance(result, dict) and (result.get("ok") is False or
                    name in {"apply_changes", "update_parameters", "build", "request_engineering"}):
                memory = record_observation({"working_memory": memory}, name, result,
                                            candidate=result.get("candidateHash") or "")
    return memory


def update_notes(memory: dict, notes: dict) -> dict:
    result = deepcopy(memory or {})
    result["notes"] = {**result.get("notes", {}), **notes}
    return result


def read_memory(memory: dict, offset: int, limit: int) -> dict:
    facts = list(reversed(list((memory.get("outcomes") or {}).values())))
    return {"outcomes": facts[offset:offset + limit], "total": len(facts),
            "nextOffset": offset + limit if offset + limit < len(facts) else None,
            "currentValidation": False}


def notes_just_saved(history: list[dict]) -> bool:
    """One-action cooldown after successful notes; intervening work/input renews it."""
    index=len(history)-1
    while index>=1:
        result=history[index]
        if result.get('role')!='tool': return False
        calls=history[index-1].get('tool_calls') or []
        if not any(call.get('id')==result.get('tool_call_id') and
            call.get('function',{}).get('name')=='update_working_notes' for call in calls):
            return False
        try:
            value=json.loads(result.get('content') or '{}')
        except (ValueError,TypeError):
            return False
        if not isinstance(value,dict): return False
        if value.get('category')=='note_progress' and value.get('ok') is False:
            index-=2  # A rejected notes call is not a different task action.
            continue
        return value.get('ok') is True and value.get('agentReportedNotes') is True
    return False
