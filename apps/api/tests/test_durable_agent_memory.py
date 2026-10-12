import json
from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command

from forma_api.agent_memory import (user_update, memory_context, record_observation,
    fit_dialogue, estimate_tokens, compact_narrative, update_notes, read_memory)
from forma_api.graphs import design
from forma_api.graphs.state import AgentState
from forma_api.tools import model_tools, parse_tool


def test_human_updates_survive_history_trimming_without_changing_exact_values():
    state = {"original_request": "Use 34.000 +/-0.010 mm centres."}
    state.update(user_update(state, "Preserve a 16.000 to 16.018 mm bore."))
    state.update(user_update(state, "Use 0.08 to 0.20 mm backlash.", "clarification"))
    history = [{"role":"user", "content":state["original_request"]}]
    history += [{"role":"assistant", "content":"read complete"}] * 80
    state["cad_history"] = design.bounded_history(history)
    assert len(state["cad_history"]) <= 30
    assert "16.000 to 16.018" in state["clarified_request"]
    assert "0.08 to 0.20" in state["clarified_request"]
    assert len(memory_context(state)["userUpdates"]) == 2
    assert user_update(state, "Use 0.08 to 0.20 mm backlash.", "clarification") == {}


@pytest.mark.asyncio
async def test_real_recovery_checkpoint_keeps_updates_across_multiple_resumes():
    graph = StateGraph(AgentState)
    graph.add_node("recovery", design.cad_recovery)
    graph.add_edge(START, "recovery")
    graph.add_edge("recovery", END)
    saver = InMemorySaver()
    config = {"configurable":{"thread_id":"human-memory"}}
    compiled = graph.compile(checkpointer=saver)
    initial = {"original_request":"Original dimensions", "final_message":"Failed build",
               "candidate_hash":"retained", "attempts":4, "model_calls":23}
    await compiled.ainvoke(initial, config)
    first = await compiled.ainvoke(Command(resume={"kind":"continue","message":"Keep 5.290 degrees."}), config)
    # A new graph instance reads the same persisted checkpoint, simulating a worker restart.
    restarted = graph.compile(checkpointer=saver)
    await restarted.ainvoke({"final_message":"Another failure"}, config)
    final = await restarted.ainvoke(Command(resume={"kind":"continue","message":"Keep 20.060 mm."}), config)
    assert "5.290" in final["clarified_request"] and "20.060" in final["clarified_request"]
    assert len(final["user_updates"]) == 2
    assert final["candidate_hash"] == first["candidate_hash"] == "retained"
    assert final["model_calls"] == 23 and final["attempts"] == 4


def test_failure_evidence_retains_provenance_after_an_edit_and_history_loss():
    error = {"ok":False,"error":{"fingerprint":"failure", "category":"geometry",
             "location":{"file":"parts/a.py","line":20},"guidance":"Fix the selector","diagnostic":"bad selector"}}
    state = {"working_memory":record_observation({}, "build", error, candidate="before")}
    state["working_memory"] = record_observation(state, "apply_changes",
        {"ok":True,"changedFiles":["parts/a.py"]}, candidate="after")
    context = memory_context(state, "after")
    old = next(item for item in context["recentOutcomes"] if item["tool"] == "build")
    assert old["candidateHash"] == "before" and old["currentCandidate"] is False
    assert context["changedFiles"]["parts/a.py"]["status"] == "edited_not_verified"
    # Historical messages without provenance cannot overwrite the recorded source hash.
    assert record_observation(state, "build", error)["outcomes"]["failure:parts/a.py"]["candidateHash"] == "before"


def test_context_budget_removes_whole_pairs_and_keeps_authoritative_request():
    prefix = [{"role":"system","content":"instructions"},
              {"role":"system","content":"Exact user requirement 15.966 to 15.984 mm."}]
    pair = [{"role":"assistant","tool_calls":[{"id":"read","function":{"name":"read_file"}}]},
            {"role":"tool","tool_call_id":"read","content":"old file" * 1000}]
    messages = [*prefix,{"role":"user","content":"original"},*pair,{"role":"user","content":"next action"}]
    result = fit_dialogue(messages, [], 300, design.protocol_safe_history)
    assert result[:2] == prefix
    assert not any(m.get("role") == "tool" for m in result)
    assert result[-1]["content"] == "next action"
    assert estimate_tokens([*result, []]) <= 300
    with pytest.raises(ValueError, match="no requirements were truncated"):
        fit_dialogue(prefix, [], 1, design.protocol_safe_history)


def test_huge_no_action_response_does_not_pollute_working_context_or_mutate_record():
    original = {"role":"assistant","content":"text"*26000,"reasoning_content":"provider-required opaque field"}
    saved = deepcopy(original)
    compacted = compact_narrative(original)
    assert len(compacted["content"]) < 500 and original == saved
    assert compacted["reasoning_content"] == original["reasoning_content"]
    action = {**original,"tool_calls":[{"id":"one"}]}
    assert compact_narrative(action) == action


def test_working_notes_are_optional_patches_not_validation_or_geometry_locks():
    notes = parse_tool("cad","update_working_notes",{"nextAction":"Inspect gear source"}).model_dump(exclude_unset=True)
    memory = {"notes":{"pendingRequirements":["true involute"],"nextAction":"read"},"outcomes":{"x":{"ok":False}}}
    patched = update_notes(memory, notes)
    assert patched["notes"]["pendingRequirements"] == ["true involute"]
    assert memory["notes"]["nextAction"] == "read"
    assert patched["outcomes"] == memory["outcomes"]
    assert "update_working_notes" in {t["function"]["name"] for t in model_tools("cad",working_memory=True)}
    assert "update_working_notes" not in {t["function"]["name"] for t in model_tools("cad")}
    with pytest.raises(ValueError):
        parse_tool("engineering","update_working_notes",{})


@pytest.mark.asyncio
async def test_multicomponent_root_repair_precedes_automatic_build(monkeypatch):
    snapshot = {"manifest":{"components":[{"id":"a","kind":"solid"},{"id":"b","kind":"solid"}],
                            "rootComponentId":"a"},"files":{"parts/a.py":"source"}}
    async def load(_): return snapshot
    async def build(_): raise AssertionError("Must repair root before automatic build")
    async def turn(_state, **kwargs):
        assert kwargs["context"]["assemblyRootRepair"]
        return None, [], {"model_calls":4}
    monkeypatch.setattr(design.run_service,"load_candidate",load)
    monkeypatch.setattr(design,"build",build)
    monkeypatch.setattr(design,"agent_tool_turn",turn)
    result = await design.cad_session({"run_id":"run","original_request":"Assembly",
                                       "cad_edits_since_build":3,"model_calls":3})
    assert result["phase"] == "cad_session"


@pytest.mark.asyncio
async def test_validation_saves_failure_outside_rolling_dialogue(monkeypatch):
    async def settings():
        from forma_api.contracts import AppSettings
        return AppSettings()
    monkeypatch.setattr(design,"app_settings",settings)
    result = await design.validate({"candidate_hash":"bad-source", "build_result":{"ok":False,
        "error":{"fingerprint":"bad","category":"geometry","guidance":"Change API call"}},"cad_history":[]})
    assert result["working_memory"]["outcomes"]["bad"]["candidateHash"] == "bad-source"


@pytest.mark.asyncio
async def test_clarifications_accumulate_instead_of_resetting_earlier_updates(monkeypatch):
    state = {"original_request":"Original", **user_update({"original_request":"Original"}, "Keep all 27 parts")}
    monkeypatch.setattr(design,"interrupt",lambda _: {"message":"Use 2 mm"})
    result = await design.clarification(state)
    assert "Keep all 27 parts" in result["clarified_request"]
    assert len(result["user_updates"]) == 2


def test_older_failures_are_retrievable_after_recent_context_is_bounded():
    state = {}
    for index in range(20):
        state["working_memory"] = record_observation(state,"build",{"ok":False,
            "error":{"fingerprint":str(index),"diagnostic":str(index)}},candidate=str(index))
    assert len(memory_context(state)["recentOutcomes"]) == 12
    older = read_memory(state["working_memory"],12,12)
    assert len(older["outcomes"]) == 8 and older["nextOffset"] is None
    assert older["outcomes"][-1]["candidateHash"] == "0"
    assert older["currentValidation"] is False


@pytest.mark.asyncio
async def test_cad_notes_tool_preserves_source_and_unrelated_pending_requirements(monkeypatch):
    snapshot = {"manifest":{"components":[],"rootComponentId":None},"files":{}}
    async def load(_): return deepcopy(snapshot)
    async def turn(_state, **kwargs):
        assert "update_working_notes" in {t["function"]["name"] for t in kwargs["tools"]}
        return {"id":"notes","name":"update_working_notes","input":{"nextAction":"Generate first part"}}, [], {"model_calls":1}
    monkeypatch.setattr(design.run_service,"load_candidate",load)
    monkeypatch.setattr(design,"agent_tool_turn",turn)
    result = await design.cad_session({"run_id":"run","original_request":"Generate assembly",
        "working_memory":{"notes":{"pendingRequirements":["All requested parts"]}}})
    assert result["working_memory"]["notes"]["pendingRequirements"] == ["All requested parts"]
    assert result["working_memory"]["notes"]["nextAction"] == "Generate first part"
    assert "candidate_hash" not in result and "validation" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize('role,workspace_key',[('cad','workspace'),('coordinator','currentWorkspace')])
async def test_model_turn_retains_full_generation_but_compacts_no_action_output(monkeypatch,role,workspace_key):
    from forma_api.contracts import AppSettings
    records = []
    prompts = []
    async def run(_): return {"id":"run"}
    async def config(_): return {"model_id":"same-model","version":1}
    async def settings(): return AppSettings()
    async def operation(_run,_key,_kind,callback): return await callback()
    async def insert(_table,value,**_kw): records.append(value)
    async def model(_config,messages,tools,**_kw):
        prompts.append(messages)
        return {"message":{"role":"assistant","content":"a"*102163},"calls":[],"inputTokens":12,"outputTokens":32768}
    monkeypatch.setattr(design,"run_row",run)
    monkeypatch.setattr(design.models,"configuration",config)
    monkeypatch.setattr(design,"app_settings",settings)
    monkeypatch.setattr(design,"operation",operation)
    monkeypatch.setattr(design.db,"insert",insert)
    monkeypatch.setattr(design.models,"turn",model)
    state = {"model_calls":0,"working_memory":{"notes":{"pendingRequirements":["Exact design"]},
        "outcomes":{"built":{"candidateHash":"current","ok":True}}}}
    _, history, usage = await design.agent_tool_turn(state,model_role=role,prompt_role=role,
        node=role+'-session',context={"request":"Exact design",workspace_key:{"candidateHash":"current"}},history=[],tools=[])
    assert len(records[0]["output"]["content"]) == 102163
    assert len(history[-1]["content"]) < 500
    assert usage["working_memory"]["notes"]["pendingRequirements"] == ["Exact design"]
    context = json.loads(prompts[0][1]["content"].split(": ",1)[1])
    assert context["workingMemory"]["notesAreAgentReported"] is True
    assert context["workingMemory"]["recentOutcomes"][0]["currentCandidate"] is True
