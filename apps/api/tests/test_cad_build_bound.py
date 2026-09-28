import pytest

from forma_api.graphs import design


@pytest.mark.asyncio
async def test_three_edits_force_intermediate_build_without_another_model_call(monkeypatch):
    captured = {}
    snapshot = {"manifest": {"components": [
        {"id": "plate", "kind": "solid"},
        {"id": "rod", "kind": "solid"},
        {"id": "assembly", "kind": "assembly"},
    ], "rootComponentId": "assembly"}, "files": {}}

    async def load(_run_id):
        return snapshot

    async def event(*_args, **_kwargs):
        return None

    async def build(state):
        captured.update(state)
        return {"phase": "validate"}

    async def model_turn(*_args, **_kwargs):
        raise AssertionError("The model must not receive another edit turn")

    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design, "build", build)
    monkeypatch.setattr(design, "agent_tool_turn", model_turn)

    result = await design.cad_session({"run_id": "run", "original_request":
        "Design Task: Assembly\nComponents (10 part types, 22 instances)",
        "cad_edits_since_build": 8, "model_calls": 20, "attempts": 2,
        "cad_history": [{"role": "user", "content": "Design it"}]})

    assert result["phase"] == "validate"
    assert captured["build_final"] is False
    assert captured["built_part_types"] == 2
    assert captured["cad_edits_since_build"] == 0
    assert captured["pending_cad_call"]["name"] == "build"
    assert captured["cad_history"][-1]["tool_calls"][0]["id"] == captured["pending_cad_call"]["id"]


@pytest.mark.asyncio
async def test_missing_root_keeps_edit_tool_available(monkeypatch):
    snapshot = {"manifest": {"components": [], "rootComponentId": None}, "files": {}}
    observed = {}

    async def load(_run_id):
        return snapshot

    async def model_turn(_state, **kwargs):
        observed["tools"] = kwargs["tools"]
        return None, [{"role": "user", "content": "Design it"}], {"model_calls": 1}

    monkeypatch.setattr(design.run_service, "load_candidate", load)
    monkeypatch.setattr(design, "agent_tool_turn", model_turn)
    result = await design.cad_session({"run_id": "run", "original_request": "Design it",
        "cad_edits_since_build": 3, "model_calls": 0})

    names = {item["function"]["name"] for item in observed["tools"]}
    assert "apply_changes" in names
    assert result["phase"] == "cad_session"
