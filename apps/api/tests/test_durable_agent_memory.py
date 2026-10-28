import json
from copy import deepcopy

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END, StateGraph
from langgraph.types import Command

from forma_api.agent_memory import (user_update, memory_context, record_observation,
    fit_dialogue, estimate_tokens, compact_narrative, update_notes, read_memory,
    capture_history)
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
    assert len(state["cad_history"]) <= 80
    assert "16.000 to 16.018" in state["clarified_request"]
    assert "0.08 to 0.20" in state["clarified_request"]
    assert memory_context(state, include_user_updates=True)["userUpdateCount"] == 2
    assert user_update(state, "Use 0.08 to 0.20 mm backlash.", "clarification") == {}


def test_file_read_index_retains_candidate_provenance_without_source_text():
    history = [{"role": "user", "content": "Inspect the housing."},
               {"role": "assistant", "tool_calls": [{"id": "read-1", "type": "function",
                   "function": {"name": "read_file", "arguments": '{"path":"parts/housing.py"}'}}]},
               {"role": "tool", "tool_call_id": "read-1", "content": json.dumps({
                   "path": "parts/housing.py", "candidateHash": "candidate-a",
                   "content": "private source text"})}]

    memory = capture_history({}, history)

    assert "private source text" not in json.dumps(memory)
    assert memory_context({"working_memory": memory}, "candidate-a")["inspectedFiles"] == [{
        "path": "parts/housing.py", "candidateHash": "candidate-a", "currentCandidate": True}]
    assert memory_context({"working_memory": memory}, "candidate-b")["inspectedFiles"][0]["currentCandidate"] is False


@pytest.mark.asyncio
async def test_real_recovery_checkpoint_keeps_updates_across_multiple_resumes(monkeypatch):
    async def load(_): return {"files": {}, "manifest": {"components": []}}
    monkeypatch.setattr(design.run_service, "load_candidate", load)

    graph = StateGraph(AgentState)
    graph.add_node("recovery", design.cad_recovery)
    graph.add_edge(START, "recovery")
    graph.add_edge("recovery", END)
    saver = InMemorySaver()
    config = {"configurable":{"thread_id":"human-memory"}}
    compiled = graph.compile(checkpointer=saver)
    initial = {"run_id":"run", "original_request":"Original dimensions", "final_message":"Failed build",
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


def test_model_context_bounds_old_outcomes_without_duplicating_full_user_updates():
    outcomes = {str(index): {"tool": "build", "candidateHash": str(index), "diagnostic": "x" * 5000,
                            "guidance": "y" * 5000} for index in range(20)}
    context = memory_context({"user_updates": [{"message": "z" * 10000}],
                              "working_memory": {"outcomes": outcomes}},
                             max_outcomes=3, text_limit=80)
    assert len(context["recentOutcomes"]) == 3
    assert all(len(item["diagnostic"]) == 80 and len(item["guidance"]) == 80
               for item in context["recentOutcomes"])
    assert "userUpdates" not in context


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


def test_failed_read_tool_feedback_is_retained_but_successful_source_is_not_copied():
    from forma_api.agent_memory import capture_history
    history = [{"role":"assistant","tool_calls":[{"id":"bad","function":{"name":"read_file"}}]},
        {"role":"tool","tool_call_id":"bad","content":json.dumps({"ok":False,
            "category":"file_not_found","message":"Use the actual workspace path."})},
        {"role":"assistant","tool_calls":[{"id":"good","function":{"name":"read_file"}}]},
        {"role":"tool","tool_call_id":"good","content":json.dumps({"ok":True,"content":"private source"})}]
    memory = capture_history({},history)
    assert memory["outcomes"]["file_not_found"]["guidance"] == "Use the actual workspace path."
    assert "private source" not in json.dumps(memory)


@pytest.mark.asyncio
@pytest.mark.parametrize('tool,arguments',[('read_file',{'path':''}),('apply_changes',{'files':{'parts/part.py':None}})])
async def test_invalid_action_waits_at_checkpoint_and_resumes_without_losing_candidate(monkeypatch,tool,arguments):
    snapshot = {"manifest":{"components":[{"id":"part","kind":"solid","source":"parts/part.py"}],
        "rootComponentId":"part"},"files":{"parts/part.py":"def build(parameters, dependencies):\n    return None"}}
    async def load(_): return deepcopy(snapshot)
    async def turn(_state, **kwargs):
        history=[{"role":"assistant","tool_calls":[{"id":"bad","type":"function",
            "function":{"name":tool,"arguments":json.dumps(arguments)}}]}]
        return {"id":"bad","name":tool,"input":arguments}, history, {"model_calls":18}
    monkeypatch.setattr(design.run_service,"load_candidate",load)
    monkeypatch.setattr(design,"agent_tool_turn",turn)
    async def seed(_): return {"phase":"cad_session"}
    monkeypatch.setattr(design,'coordinator',seed)
    monkeypatch.setattr(design,'coordinator_session',seed)
    saver=InMemorySaver()
    compiled=design.build_graph(saver)
    config={"configurable":{"thread_id":"invalid-action-resume"}}
    initial={"run_id":"run","original_request":"Keep the part","candidate_hash":"same",
        "recovery_plan_count":6,
        "attempts":5,"model_calls":17,"cad_invalid_tool_attempts":2,
        "cad_contract_repair":{"issues":[{"message":"Keep this unresolved contract"}],"stalledAttempts":2},
        "requirements":[{"id":"exact"}],"working_memory":{"notes":{"pendingRequirements":["Keep exact dimensions"]}}}
    await compiled.ainvoke(initial,config)
    await compiled.ainvoke(None,config)
    await compiled.ainvoke(None,config)
    await compiled.ainvoke(None,config)  # Exhausted automatic re-plan routes to the durable user interrupt.
    waiting=await compiled.ainvoke(None,config)
    assert waiting['__interrupt__'] and waiting['phase']=='cad_recovery'
    assert waiting['model_calls']==18 and waiting['cad_invalid_tool_attempts']==3
    assert 'terminal_status' not in waiting
    feedback=json.loads(waiting['cad_history'][-1]['content'])
    assert feedback['tool']==tool and feedback['issues']
    assert 'input_value' not in json.dumps(feedback)
    assert ('path' if tool=='read_file' else 'files') in feedback['acceptedArguments']
    if tool=='apply_changes':
        assert 'manifest/source' in feedback['repairGuidance']
    restarted=design.build_graph(saver)
    resumed=await restarted.ainvoke(Command(resume={"kind":"continue","message":"Read parts/part.py."}),config)
    assert resumed['candidate_hash']=='same' and resumed['attempts']==5 and resumed['model_calls']==18
    assert resumed['requirements']==initial['requirements']
    assert resumed['working_memory']['notes']==initial['working_memory']['notes']
    assert resumed['working_memory']['outcomes']['tool_contract']['candidateHash']==design.digest(snapshot)
    assert resumed['working_memory']['outcomes']['tool_contract']['issues']==feedback['issues']
    assert resumed['cad_invalid_tool_attempts']==0
    assert resumed['cad_contract_repair']['stalledAttempts']==0
    assert resumed['cad_contract_repair']['issues']==initial['cad_contract_repair']['issues']
    assert resumed['user_updates']==[{"kind":"continuation","message":"Read parts/part.py."}]


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
    async def operation(_run,_key,_kind,callback,**kwargs): return await callback()
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
