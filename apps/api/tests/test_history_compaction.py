import json

from forma_api.graphs.design import bounded_history


def test_completed_cad_edit_keeps_outcome_without_checkpointing_source():
    source = "def build(parameters, dependencies):\n" + "    pass\n" * 20_000
    call = {"id": "edit-1", "type": "function", "function": {"name": "apply_changes",
            "arguments": json.dumps({"files": {"parts/bracket.py": source}})}}
    history = [
        {"role": "user", "content": "Make a bracket"},
        {"role": "assistant", "content": None, "tool_calls": [call]},
        {"role": "tool", "tool_call_id": "edit-1", "content": json.dumps({
            "ok": True, "candidateHash": "candidate-123", "changedFiles": ["parts/bracket.py"]})},
    ]

    compacted = bounded_history(history)

    assert len(json.dumps(compacted)) < 2000
    assert "candidate-123" in compacted[-1]["content"]
    assert "parts/bracket.py" in compacted[-1]["content"]
    assert source not in json.dumps(compacted)
    assert all(item.get("role") != "tool" for item in compacted)


def test_pending_or_failed_edit_keeps_tool_protocol_for_repair():
    call = {"id": "edit-2", "type": "function", "function": {"name": "apply_changes",
            "arguments": json.dumps({"files": {"parts/bad.py": "broken"}})}}
    pending = [{"role": "assistant", "content": None, "tool_calls": [call]}]
    assert bounded_history(pending, allow_pending=True) == pending
    failed = [*pending, {"role": "tool", "tool_call_id": "edit-2",
                         "content": json.dumps({"ok": False, "error": "SyntaxError"})}]
    assert bounded_history(failed) == failed
