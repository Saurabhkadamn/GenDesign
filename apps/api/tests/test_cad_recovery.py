import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command

from forma_api.execution import build_error
from forma_api.graphs import design
from forma_api.graphs.state import AgentState


def test_empty_native_diagnostic_preserves_exit_and_distinguishes_signals():
    segmentation = build_error({"exitCode": -11, "diagnostic": "", "clean": True}, "build")
    killed = build_error({"exitCode": -9, "diagnostic": "", "clean": True}, "build")
    assert segmentation["category"] == "worker_signal"
    assert segmentation["diagnostic"] and segmentation["exitCode"] == -11
    assert segmentation["fingerprint"] != killed["fingerprint"]
    assert "does not establish" in segmentation["guidance"]
    assert build_error({"exitCode": 124, "timedOut": True}, "build")["category"] == "timeout"


def test_pre_export_invalid_component_has_actionable_profile_guidance():
    error = build_error({"exitCode": 1, "diagnostic": "Invalid B-rep component housing from parts/housing.py"}, "build")
    assert error["category"] == "invalid_component"
    assert "separate valid solid cutters" in error["guidance"]


@pytest.mark.asyncio
async def test_successful_milestone_resets_only_repair_allowance():
    result = await design.validate({"repairs": 2, "attempts": 5, "model_calls": 20,
        "candidate_hash": "accepted", "build_final": False, "build_result": {"ok": True}})
    assert result["phase"] == "cad_session" and result["repairs"] == 0
    assert result["last_milestone_hash"] == "accepted"
    assert "attempts" not in result and "model_calls" not in result


@pytest.mark.asyncio
async def test_durable_recovery_waits_then_resumes_same_candidate_without_rebuild():
    graph = StateGraph(AgentState)
    graph.add_node("recovery", design.cad_recovery)
    graph.add_edge(START, "recovery")
    graph.add_edge("recovery", END)
    compiled = graph.compile(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "repair-test"}}
    state = {"final_message": "Worker failed; candidate retained", "candidate_hash": "same-draft",
        "repairs": 3, "attempts": 3, "model_calls": 17, "last_failed_candidate": {"candidate": "same-draft"},
        "build_result": {"ok": False}, "cad_history": [], "cad_edits_since_build": 3}
    waiting = await compiled.ainvoke(state, config)
    assert waiting["__interrupt__"] and waiting["repairs"] == 3
    resumed = await compiled.ainvoke(Command(resume={"kind": "continue"}), config)
    assert resumed["phase"] == "cad_session" and resumed["repairs"] == 0
    assert resumed["candidate_hash"] == "same-draft"
    assert resumed["last_failed_candidate"] == state["last_failed_candidate"]
    assert resumed["attempts"] == 3 and resumed["model_calls"] == 17
    assert resumed["build_result"]["ok"] is False


@pytest.mark.asyncio
async def test_rejection_does_not_restart_automatic_repairs(monkeypatch):
    monkeypatch.setattr(design, "interrupt", lambda _: {"kind": "rejection"})
    with pytest.raises(design.Pause, match="Continue is required"):
        await design.cad_recovery({"final_message": "Recovery"})
