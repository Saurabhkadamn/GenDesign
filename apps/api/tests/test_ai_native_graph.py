import json
from copy import deepcopy

import pytest

from forma_api.graphs import design
from forma_api.harness import observation_epoch
from forma_api.contracts import Limits


def state(**updates):
    value = {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "project_id": "00000000-0000-0000-0000-000000000002",
        "owner_id": "00000000-0000-0000-0000-000000000003",
        "base_revision_id": None,
        "original_request": "Create a block.",
        "model_calls": 0,
        "search_count": 0,
        "cad_history": [{"role": "user", "content": "Create a block."}],
        "requirements": [],
    }
    value.update(updates)
    return value


def test_research_checkpoint_preserves_exact_requirement_bindings():
    requirements = [{'id':'origin','kind':'center','description':'Keep grounding unchanged',
                     'componentId':'explicit_datum','center':[1,2,3]}]
    snapshot = {'manifest':{'nativeAssembly':{'groundedInstances':['root']},'rootComponentId':'root'},'files':{}}
    result = design.checkpoint_view(state(harness_version=4,requirements=requirements),snapshot)
    assert result['requirements'] == requirements
    assert result['requirements'] is not requirements


@pytest.mark.asyncio
async def test_research_recovery_returns_evidence_to_lead_without_another_fixed_repair_plan(monkeypatch):
    from forma_api.graphs import recovery
    snapshot = {'manifest':{'components':[]},'files':{'parts/broken.py':'source'}}
    async def load(_run): return snapshot
    async def row(_state): return {'id':'run','project_id':'project'}
    async def record(*args, **kwargs): return {'id':'recovery-record'}
    async def event(*args, **kwargs): pass
    monkeypatch.setattr(design.run_service,'load_candidate',load)
    monkeypatch.setattr(design,'run_row',row)
    monkeypatch.setattr(recovery,'append_record',record)
    monkeypatch.setattr(design.repo,'event',event)
    pending = {'id':'delegate','name':'delegate'}
    result = await design.cad_replan(state(harness_version=4,research_mode=True,
        coordinator_pending_call=pending,coordinator_history=[{'role':'assistant','tool_calls':[{
            'id':'delegate','type':'function','function':{'name':'delegate','arguments':'{}'}}]}]))
    assert result['phase']=='coordinator_session'
    response = json.loads(result['coordinator_history'][-1]['content'])
    assert response['ok'] is False and response['recordId']=='recovery-record'
    assert result['coordinator_pending_call']=={} and result['final_message']==''


@pytest.mark.asyncio
async def test_multiple_provider_calls_checkpoint_remaining_actions(monkeypatch, graph_mocks):
    calls = [
        {"id": "read-first", "name": "read_file", "input": {"path": "parts/first.py"}},
        {"id": "read-second", "name": "read_file", "input": {"path": "parts/second.py"}},
        {"id": "edit-third", "name": "apply_changes", "input": {"files": {"parts/third.py": "secret source"}}},
    ]

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"id": c["id"], "type": "function", "function": {
                "name": c["name"], "arguments": json.dumps(c["input"])}} for c in calls]},
            "calls": calls, "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    call, history, usage = await design.agent_tool_turn(state(), model_role="cad", prompt_role="cad",
        node="cad-session", context={}, history=[], tools=[])
    assert call["id"] == "read-first"
    assert [c["id"] for c in history[-1]["tool_calls"]] == ["read-first"]
    feedback = design.tool_message(call, {"ok": True, "content": "first source"})
    execution = json.loads(feedback["content"])["toolExecution"]
    assert execution["queued"] == [
        {"id": "read-second", "name": "read_file"}, {"id": "edit-third", "name": "apply_changes"}]
    assert [item["call"] for item in usage["cad_pending_actions"]] == calls[1:]
    assert "not completed evidence" in execution["instruction"]
    assert "secret source" not in feedback["content"]
    assert "deferred_calls" not in calls[0]  # cached provider result remains immutable
    assert usage["model_calls"] == 1
    assert "candidate" not in graph_mocks  # no additional edit happened


@pytest.mark.asyncio
async def test_research_multi_call_preserves_signed_message_and_all_tool_relationships(monkeypatch, graph_mocks):
    calls = [{'id':'first','name':'read_file','input':{'path':'parts/a.py'}},
             {'id':'second','name':'read_file','input':{'path':'parts/b.py'}}]
    message = {'role':'assistant','content':'Inspect both files.',
        'reasoning_details':[{'type':'reasoning.encrypted','data':'opaque-signature'}],
        'tool_calls':[{'id':c['id'],'type':'function','function':{'name':c['name'],
            'arguments':json.dumps(c['input'])}} for c in calls]}
    async def turn(*args,**kwargs):
        return {'message':deepcopy(message),'calls':deepcopy(calls),'inputTokens':10,'outputTokens':20}
    monkeypatch.setattr(design.models,'turn',turn)
    call, history, usage = await design.agent_tool_turn(state(harness_version=4,research_mode=True),
        model_role='cad',prompt_role='cad',node='cad-session',context={},history=[],tools=[])
    assert history[0]==message
    assert usage['cad_pending_actions']==[]
    assert json.loads(history[1]['content'])['category']=='deferred'
    complete = design.bounded_history([*history,design.tool_message(call,{'ok':True,'content':'source A'})])
    assert complete[0]==message
    assert {m['tool_call_id'] for m in complete[1:]}=={'first','second'}


@pytest.mark.asyncio
async def test_research_actionless_reasoning_is_saved_without_eviction(monkeypatch, graph_mocks):
    original = {"role": "assistant", "content": "",
                "reasoning_details": [{"type": "reasoning.text", "text": "thinking" * 12_000}]}
    saved = {}

    async def turn(*_args, **_kwargs):
        return {"message": deepcopy(original), "calls": [], "inputTokens": 10,
                "outputTokens": 16_384, "webSearchRequests": 0}

    async def save_generation(_run, record):
        saved.update(record)

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "save_generation", save_generation)
    call, history, _usage = await design.agent_tool_turn(
        state(harness_version=4, research_mode=True), model_role="engineering",
        prompt_role="engineering", node="engineering-session", context={},
        history=[{"role": "user", "content": "Read the candidate and calculate."}], tools=[])

    assert call is None
    assert saved["output"] == original
    assert len(json.dumps(history)) < 1000
    assert "reasoning_details" not in history[-1]


@pytest.mark.asyncio
async def test_research_structured_correction_has_no_repair_counter_stop(monkeypatch, graph_mocks):
    from forma_api.graphs import structured
    from forma_api.contracts import Contract
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph,START,END
    from forma_api.graphs.state import AgentState
    from forma_api.graphs.jobs import durable_node
    from forma_api.services.runs import lease_context
    class Result(Contract):
        value: int
    calls, records, jobs = [], [], {}
    async def turn(config,messages,tools,**kwargs):
        calls.append(deepcopy(messages))
        arguments = {'value':'not-an-integer' if len(calls)<4 else 11}
        return {'calls':[{'id':f'call-{len(calls)}','name':'submit','input':arguments}],
            'message':{'role':'assistant','content':'','reasoning_details':[{'type':'reasoning.encrypted','data':f'signature-{len(calls)}'}],
                'tool_calls':[{'id':f'call-{len(calls)}','type':'function','function':{
                    'name':'submit','arguments':json.dumps(arguments)}}]},'inputTokens':10,'outputTokens':20}
    async def record(*args,**kwargs): records.append(args)
    async def rpc(name,args):
        if name=='check_research_fence':
            return True
        if name=='record_research_generation':
            return None
        if name=='begin_research_job':
            return jobs.get(args['p_key'],{'status':'running'})
        assert name=='commit_research_job'
        jobs[args['p_key']]={'status':'complete','result':args['p_result']}
        return 0
    async def session(input_state):
        result,usage = await design.structured_turn(input_state,'engineering','analysis','Calculate from the brief.','submit',Result)
        assert result.value==11
        return {**usage,'phase':'done'}
    monkeypatch.setattr(design.models,'turn',turn)
    monkeypatch.setattr(design.db,'rpc',rpc)
    monkeypatch.setattr(structured,'append_record',record)
    builder = StateGraph(AgentState)
    builder.add_node('analysis',durable_node('analysis',session))
    builder.add_edge(START,'analysis')
    builder.add_conditional_edges('analysis',lambda s:END if s['phase']=='done' else 'analysis')
    saver = InMemorySaver()
    config = {'configurable':{'thread_id':'structured-recovery'}}
    initial = state(harness_version=4,research_mode=True,phase='analysis')
    token = lease_context.set({'run_id':initial['run_id'],'worker':'w','generation':1,'candidate_version':0})
    try:
        # A distinct graph instance resumes each scheduling boundary.
        for index in range(4):
            result = await builder.compile(checkpointer=saver,interrupt_after='*').ainvoke(initial if index==0 else None,config)
        assert result['phase']=='done' and result['model_calls']==4
        assert len(records)==3
        prior_assistant = next(m for m in calls[1] if m['role']=='assistant')
        assert prior_assistant['reasoning_details'][0]['data']=='signature-1'
        assert any(m.get('tool_call_id')=='call-1' for m in calls[1])
    finally:
        lease_context.reset(token)


@pytest.mark.asyncio
async def test_continued_cycle_keeps_lifetime_calls_but_grants_fresh_budget(monkeypatch, graph_mocks):
    async def turn(*_args, **_kwargs):
        call = {"id": "continued-read", "name": "read_file", "input": {"path": "parts/current.py"}}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": json.dumps(call["input"])}}]},
            "calls": [call], "inputTokens": 10, "outputTokens": 2, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    call, _, usage = await design.agent_tool_turn(
        state(model_calls=96, model_calls_this_cycle=0), model_role="cad", prompt_role="cad",
        node="cad-session", context={}, history=[], tools=[])
    assert call["name"] == "read_file"
    assert usage["model_calls"] == 97
    assert usage["model_calls_this_cycle"] == 1


@pytest.mark.asyncio
async def test_agent_tool_turn_stops_at_current_cycle_limit(monkeypatch, graph_mocks):
    async def unexpected_turn(*_args, **_kwargs):
        pytest.fail("the model must not be called after this cycle's limit")

    monkeypatch.setattr(design.models, "turn", unexpected_turn)
    with pytest.raises(design.Pause, match="budget for this cycle"):
        await design.agent_tool_turn(
            state(model_calls=96, model_calls_this_cycle=24), model_role="cad", prompt_role="cad",
            node="cad-session", context={}, history=[], tools=[])


def test_single_tool_feedback_keeps_original_protocol():
    result = {"ok": True, "content": "source"}
    message = design.tool_message({"id": "one", "name": "read_file"}, result)
    assert json.loads(message["content"]) == result


@pytest.fixture
def graph_mocks(monkeypatch):
    saved = {}

    async def run_row(_state):
        return {"id": _state["run_id"]}

    async def settings():
        return design.AppSettings()

    async def configuration(_role):
        return {"model_id": "test/model", "version": 1, "api_key": "unused"}

    async def execute(_run, _key, _kind, callback, **_kwargs):
        return await callback()

    async def insert(*_args, **_kwargs):
        return []

    async def event(*_args, **_kwargs):
        return None

    async def load_candidate(_run_id):
        return saved.get("candidate", {"manifest": {
            "schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": [],
        }, "files": {}})

    async def save_candidate(_run_id, candidate, candidate_hash):
        saved["candidate"] = candidate
        saved["hash"] = candidate_hash

    monkeypatch.setattr(design, "run_row", run_row)
    monkeypatch.setattr(design, "app_settings", settings)
    monkeypatch.setattr(design.models, "configuration", configuration)
    monkeypatch.setattr(design, "operation", execute)
    monkeypatch.setattr(design.db, "insert", insert)
    monkeypatch.setattr(design.repo, "event", event)
    monkeypatch.setattr(design.run_service, "load_candidate", load_candidate)
    monkeypatch.setattr(design.run_service, "save_candidate", save_candidate)
    return saved


@pytest.mark.asyncio
@pytest.mark.parametrize("parameter", ["thickness", "unknown"])
async def test_cad_parameter_tool_preserves_source_and_clears_only_stale_evidence(monkeypatch, graph_mocks, parameter):
    original = design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "plate", "name": "Plate", "source": "parts/plate.py",
            "kind": "solid", "parameters": {"thickness": 4}}], "rootComponentId": "plate"},
        "files": {"parts/plate.py": "def build(p,d): return p['thickness']"}}).model_dump()
    graph_mocks["candidate"] = deepcopy(original)
    arguments = {"changes": [{"componentId": "plate", "parameter": parameter, "value": 3}]}

    async def turn(_config, _messages, tools, **_kwargs):
        assert "update_parameters" in {tool["function"]["name"] for tool in tools}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "parameter-edit", "type": "function", "function": {
                "name": "update_parameters", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "parameter-edit", "name": "update_parameters", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(validation={"old": True}, build_result={"ok": True}))
    assert graph_mocks["candidate"]["files"] == original["files"]
    if parameter == "unknown":
        assert graph_mocks["candidate"] == original
        assert "validation" not in result and "build_result" not in result
        assert "unknown existing parameter" in result["cad_history"][-1]["content"]
    else:
        expected = deepcopy(original)
        expected["manifest"]["components"][0]["parameters"]["thickness"] = 3
        assert graph_mocks["candidate"] == expected
        assert result["validation"] == {} and result["build_result"] == {}
        assert result["cad_edits_since_build"] == 1


def grounded_review_candidate():
    frame = {"position": [0, 0, 0], "rotation": [0, 0, 0]}
    return design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid"},
            {"id": "assembly", "name": "Assembly", "source": "assemblies/grid.py", "kind": "assembly", "dependencies": ["plate"]}],
        "rootComponentId": "assembly",
        "instances": [{"id": "ground", "definitionId": "plate", "name": "Ground", "frame": frame},
            {"id": "other", "definitionId": "plate", "name": "Other", "frame": {**frame, "position": [13, 2, 3]}}],
        "references": [{"id": "datum", "componentId": "plate", "kind": "datum", "frame": frame}],
        "joints": [{"id": "mate", "kind": "fixed", "referenceA": "datum", "referenceB": "datum", "occurrenceA": "ground", "occurrenceB": "other"}],
        "nativeAssembly": {"groundedInstances": ["ground"], "allowedDof": 0}},
        "files": {"parts/plate.py": "def build(p,d): return None", "assemblies/grid.py": "def build(p,d): return None"}}).model_dump()


def test_grounding_facts_use_exact_occurrence_and_reject_stale_report():
    snapshot = grounded_review_candidate()
    report = {"identity": design.identity(snapshot, []),
        "nativeAssembly": {"engine": "OndselSolver", "solvedFrames": 1},
        "inspection": {"boundsCentreMm": [54, 30, 0]}}
    facts = design.native_grounding_evidence(snapshot, {"report": report})
    assert facts["nativeGroundingCheckedForCurrentCandidate"] is True
    assert list(facts["groundedOccurrences"]) == ["ground"]
    assert facts["groundedOccurrences"]["ground"]["declaredFrame"]["position"] == (0, 0, 0)
    assert facts["groundedOccurrences"]["ground"]["coordinateSystem"] == "world"
    report["identity"]["candidate"] = "stale"
    assert design.native_grounding_evidence(snapshot, {"report": report})["nativeGroundingCheckedForCurrentCandidate"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["current", "unknown", "stale", "repeated"])
async def test_disputed_review_can_be_reassessed_once_without_editing_or_publishing(monkeypatch, graph_mocks, case):
    snapshot = grounded_review_candidate()
    graph_mocks["candidate"] = deepcopy(snapshot)
    finding = {"id": "grounding", "status": "observed_mismatch", "evidence": ["assembly centre"],
        "statement": "Ground moved", "repair_instruction": "Move the ground", "explanation": "Claimed mismatch"}
    review = {"action": "repair", "summary": "Grounding claim", "findings": [finding]}
    arguments = {"findingId": "missing" if case == "unknown" else "grounding",
        "explanation": "The exact ground frame is still zero; the aggregate centre belongs to the whole assembly.",
        "evidence": ["ground frame [0,0,0]", "current native grounding check passed"]}
    validated = {"identity": design.identity(snapshot, [])}
    if case == "stale":
        validated["identity"]["candidate"] = "stale"
    response_key = f"{design.digest(snapshot)}:grounding"

    async def turn(_config, _messages, tools, **_kwargs):
        assert "respond_to_review" in {t["function"]["name"] for t in tools}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{"id": "response", "type": "function",
            "function": {"name": "respond_to_review", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "response", "name": "respond_to_review", "input": arguments}], "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(review=review, validation=validated,
        reviewed_candidate_hash=design.digest(snapshot), review_responses=[response_key] if case == "repeated" else []))
    assert graph_mocks["candidate"] == snapshot
    assert "validation" not in result and "published_revision_id" not in result
    if case == "current":
        assert result["phase"] == "review_session"
        assert result["review_responses"] == [response_key]
        assert result["review_response"]["candidateHash"] == design.digest(snapshot)
    else:
        assert result["phase"] == "cad_session" and "review_response" not in result


def test_review_response_is_not_advertised_outside_a_review_repair():
    from forma_api.tools import model_tools
    assert "respond_to_review" not in {tool["function"]["name"] for tool in model_tools("cad")}


@pytest.mark.asyncio
async def test_cad_session_applies_one_incremental_source_patch(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "block", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/block.py",
        "content": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,10)"}],
        "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "patch-1", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "patch-1", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert result["phase"] == "cad_session"
    assert graph_mocks["candidate"]["manifest"]["rootComponentId"] == "block"
    assert "parts/block.py" in graph_mocks["candidate"]["files"]
    assert result["validation"] == {}


@pytest.mark.asyncio
async def test_exact_text_patch_mismatch_switches_to_full_file_replacement(monkeypatch, graph_mocks):
    original = design.Snapshot.model_validate({"manifest": {"components": [
        {"id": "cover", "name": "Cover", "kind": "solid", "source": "parts/cover.py",
         "dependencies": [], "parameters": {}}], "rootComponentId": "cover"},
        "files": {"parts/cover.py": "def build(parameters, dependencies):\n    return None"}}).model_dump()
    graph_mocks["candidate"] = deepcopy(original)
    first_arguments = {"patches": [{"path": "parts/cover.py", "oldText": "return False", "newText": "return cq.Workplane('XY').box(1, 1, 1)"}]}

    async def failed_patch(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "bad-patch", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(first_arguments)}}]},
            "calls": [{"id": "bad-patch", "name": "apply_changes", "input": first_arguments}],
            "inputTokens": 10, "outputTokens": 5, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", failed_patch)
    rejected = await design.cad_session(state())
    assert graph_mocks["candidate"] == original
    assert rejected["cad_patch_mismatch"]["paths"] == ["parts/cover.py"]
    failure = json.loads(rejected["cad_history"][-1]["content"])
    assert failure["category"] == "source_patch_mismatch"
    assert "nothing was saved" in failure["repairGuidance"]

    updated_source = "def build(parameters, dependencies):\n    return None\n# repaired from full current source\n"
    full_arguments = {"files": {"parts/cover.py": updated_source}}
    observed = {}

    async def full_file(_config, messages, tools, **_kwargs):
        context = json.loads(messages[1]["content"].split(": ", 1)[1])
        observed["context"] = context
        edit_tool = next(item for item in tools if item["function"]["name"] == "apply_changes")
        observed["schema"] = edit_tool["function"]["parameters"]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "full-file", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(full_arguments)}}]},
            "calls": [{"id": "full-file", "name": "apply_changes", "input": full_arguments}],
            "inputTokens": 10, "outputTokens": 5, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", full_file)
    accepted = await design.cad_session({**state(), **rejected})
    assert accepted["phase"] == "cad_session"
    assert graph_mocks["candidate"]["files"]["parts/cover.py"] == updated_source
    assert accepted["cad_patch_mismatch"] == {}
    assert "patches" not in observed["schema"]["properties"]
    assert "parts/cover.py" in observed["context"]["sourceEditRecovery"]["paths"]
    assert "omit patches" in observed["context"]["sourceEditRecovery"]["instruction"]


def test_legacy_patch_mismatch_is_recovered_from_history_until_a_source_edit():
    snapshot = {"manifest": {"components": []}, "files": {"parts/front_cover.py": "current source"}}
    history = [{"role": "tool", "content": json.dumps({"ok": False, "category": "tool_contract",
        "issues": [{"path": "", "message": "Patch for parts/front_cover.py must match exactly once; read current source and retry."}]})}]
    current = design.active_source_patch_mismatch({"cad_history": history}, snapshot)
    assert current["paths"] == ["parts/front_cover.py"]
    changed = [*history, {"role": "tool", "content": json.dumps({"ok": True,
        "candidateHash": "new-hash", "changedFiles": ["parts/front_cover.py"]}),
        "tool_call_id": "successful-edit"}]
    assert design.active_source_patch_mismatch({"cad_history": changed}, snapshot) is None


@pytest.mark.asyncio
async def test_new_run_loads_prior_brief_and_revision_for_followup(monkeypatch, graph_mocks):
    async def run_row(_state):
        return {"id": _state["run_id"], "project_id": _state["project_id"],
            "owner_id": _state["owner_id"], "base_revision_id": "revision-1",
            "selected_ids": ["outer_race"]}

    async def snapshot(_revision_id):
        return {"manifest": {"schemaVersion": 1, "units": "mm", "components": [],
            "instances": [], "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {"parts/race.py": "def build(p,d): pass"}}

    async def context(*_args):
        return {"previousMessages": [{"role": "user", "content": "Design the original bearing."}],
            "revision": {"id": "revision-1", "allRequirementsVerified": False}}

    monkeypatch.setattr(design, "run_row", run_row)
    monkeypatch.setattr(design.repo, "load_snapshot", snapshot)
    monkeypatch.setattr(design.repo, "agent_context", context)
    result = await design.coordinator(state(original_request="Change the outer race groove."))
    assert result["phase"] == "coordinator_session"
    assert result["project_context"]["previousMessages"][0]["content"] == "Design the original bearing."
    assert result["project_context"]["sourceFiles"] == ["parts/race.py"]


@pytest.mark.asyncio
async def test_followup_cad_delegation_preserves_original_brief_and_selection(monkeypatch, graph_mocks):
    arguments = {"role": "cad", "task": "Enlarge only the outer race groove."}
    async def turn(_config, messages, _tools, **_kwargs):
        assert "Design the original bearing" in messages[1]["content"]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate-1", "type": "function", "function": {
                "name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate-1", "name": "delegate", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session(state(
        original_request="Change its outer race groove.", selected_ids=["outer_race"],
        project_context={"previousMessages": [{"role": "user",
            "content": "Design the original bearing."}]},
        coordinator_history=[{"role": "user", "content": "Change its outer race groove."}],
    ))
    assert result["phase"] == "cad_session"
    delegated = json.loads(result["cad_history"][0]["content"])
    assert delegated["originalBrief"] == "Design the original bearing."
    assert delegated["selectedIds"] == ["outer_race"]
    assert "outer race" in delegated["delegatedTask"]


@pytest.mark.asyncio
async def test_old_checkpoint_refreshes_accepted_dimensions_once(monkeypatch, graph_mocks):
    refreshed = []
    owned_context = {"revision": {"id": "r1", "componentMeasurements": {
        "plate": {"dimensions": [8, 8, 4], "solids": 1, "valid": True}}}}

    async def context(project, owner, run, revision, selected):
        assert project.endswith("2") and owner.endswith("3") and revision == "r1"
        refreshed.append(run)
        return owned_context

    async def turn(_config, messages, _tools, **_kwargs):
        assert "componentMeasurements" in messages[1]["content"]
        assert "[8, 8, 4]" in messages[1]["content"]
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "inspect", "type": "function", "function": {
                "name": "inspect_project", "arguments": "{}"}}]},
            "calls": [{"id": "inspect", "name": "inspect_project", "input": {}}],
            "inputTokens": 1, "outputTokens": 1, "webSearchRequests": 0}

    monkeypatch.setattr(design.repo, "agent_context", context)
    monkeypatch.setattr(design.models, "turn", turn)
    initial = state(base_revision_id="r1", project_context={"revision": {"id": "r1"}})
    result = await design.coordinator_session(initial)
    assert result["project_context"] == owned_context
    assert json.loads(result["coordinator_history"][-1]["content"]) == owned_context
    await design.coordinator_session({**initial, **result})
    assert len(refreshed) == 1


@pytest.mark.asyncio
async def test_cad_session_rejects_malformed_source_before_persisting(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "block", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/block.py",
        "content": "import cadquery as cq  def build(p,d): return cq.Workplane(XY).box(10,10,10)"}],
        "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "bad-source", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "bad-source", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert "python_syntax" in result["cad_history"][-1]["content"]
    assert "candidate" not in graph_mocks


@pytest.mark.asyncio
async def test_cad_session_feedback_breaks_repeated_read_loop(monkeypatch, graph_mocks):
    arguments = {"path": "parts/block.py"}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "read-again", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "read-again", "name": "read_file", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    prior = [
        {"role": "user", "content": "Create a block."},
        {"role": "assistant", "tool_calls": [{"id": "old-1", "type": "function",
            "function": {"name": "read_file", "arguments": json.dumps(arguments)}}]},
        {"role": "tool", "tool_call_id": "old-1", "content": "{}"},
    ]
    result = await design.cad_session(state(cad_history=prior))
    assert "repeated_tool_action" in result["cad_history"][-1]["content"]


@pytest.mark.asyncio
async def test_cad_session_normalizes_model_root_sentinels(monkeypatch, graph_mocks):
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [
            {"id": "assembly", "name": "Assembly", "source": "assemblies/main.py",
             "kind": "assembly", "dependencies": ["block"], "parameters": {}, "color": "#b9c4ad"},
            {"id": "block", "name": "Block", "source": "parts/block.py",
             "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"},
        ],
        "instances": [
            {"id": "assembly_inst", "definitionId": "assembly", "parentId": "__root__",
             "name": "Assembly", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
            {"id": "block_inst", "definitionId": "block", "parentId": "assembly_inst",
             "name": "Block", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
        ],
        "rootComponentId": "assembly", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": {
        "parts/block.py": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,10)",
        "assemblies/main.py": "def build(p,d): return d['block']",
    }, "manifest": manifest, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "patch-root", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "patch-root", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    stored = {item["id"]: item for item in graph_mocks["candidate"]["manifest"]["instances"]}
    assert stored["assembly_inst"]["parentId"] is None
    assert stored["block_inst"]["parentId"] == "assembly_inst"
    assert '"hierarchyNormalized": true' in result["cad_history"][-1]["content"]


@pytest.mark.asyncio
async def test_cad_session_enforces_calculation_role_boundary(monkeypatch, graph_mocks):
    arguments = {"files": {"calculations/analysis.py": "def calculate(): return {}"},
                 "manifest": None, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "bad-cad-edit", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "bad-cad-edit", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    assert "role_boundary" in result["cad_history"][-1]["content"]
    assert "candidate" not in graph_mocks


@pytest.mark.asyncio
async def test_cad_can_revisit_engineering_for_unchanged_workspace(monkeypatch, graph_mocks):
    arguments = {"task": "Rerun the same calculation."}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "repeat-engineering", "type": "function", "function": {
                "name": "request_engineering", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "repeat-engineering", "name": "request_engineering", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    empty = await design.run_service.load_candidate(state()["run_id"])
    result = await design.cad_session(state(
        engineering_summary="Already analyzed.", engineering_candidate_hash=design.digest(empty)))
    assert result["phase"] == "engineering_analysis"
    assert result["pending_cad_call"]["name"] == "request_engineering"



@pytest.mark.asyncio
async def test_engineering_bad_tool_stays_repairable(monkeypatch, graph_mocks):
    async def turn(*args, **kwargs):
        call = {"id":"bad", "name":"calculate", "input":{"path":"not-a-file"}}
        history = [{"role":"user","content":"Calculate"},{"role":"assistant","tool_calls":[{"id":"bad","type":"function","function":{"name":"calculate","arguments":json.dumps(call["input"])}}]}]
        return call, history, {"model_calls":1}
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    result = await design.engineering_analysis(state(original_request="Calculate the bracket stress."))
    assert result["phase"] == "engineering_analysis"
    feedback = json.loads(result["engineering_history"][-1]["content"])
    assert feedback["ok"] is False
    assert "repair" in feedback["instruction"].lower() or "correct" in feedback["instruction"].lower()



@pytest.mark.asyncio
async def test_engineering_calculation_prepares_stable_sandbox_before_execution(monkeypatch, graph_mocks):
    observed = []

    async def turn(*_args, **_kwargs):
        call = {"id": "calculation", "name": "calculate", "input": {"path": "calculations/analysis.py"}}
        assistant = {"role": "assistant", "tool_calls": [{"id": call["id"], "type": "function",
                     "function": {"name": call["name"], "arguments": json.dumps(call["input"])}}]}
        return call, [{"role": "user", "content": "Calculate."}, assistant], {"model_calls": 1}

    async def execute(_run, cp, _call, _settings, _worker):
        observed.append(cp["sandbox"])
        return {"ok": False, "error": {"category": "calculation_error"}}

    monkeypatch.setattr(design, "agent_tool_turn", turn)
    monkeypatch.setattr(design, "execute_tool", execute)
    first = await design.engineering_analysis(state(harness_version=4, research_mode=True))
    second = await design.engineering_analysis(state(harness_version=4, research_mode=True))

    assert observed == ["forma-00000000000000000000000000000001-engineering"] * 2
    assert first["sandbox"] == observed[0]
    assert second["phase"] == "engineering_analysis"


@pytest.mark.asyncio
async def test_engineering_rejected_edit_commits_failure_receipt(monkeypatch, graph_mocks):
    recorded = []

    async def turn(*_args, **_kwargs):
        call = {"id": "invalid-edit", "name": "apply_changes",
                "input": {"files": {"calculations/analysis.py": "def calculate(): return {}"},
                          "manifest": None, "deletePaths": []}}
        assistant = {"role": "assistant", "tool_calls": [{"id": call["id"], "type": "function",
                     "function": {"name": call["name"], "arguments": json.dumps(call["input"])}}]}
        return call, [{"role": "user", "content": "Edit the calculation."}, assistant], {"model_calls": 1}

    async def reject(*_args, **_kwargs):
        raise ValueError("Engineering can edit only calculations/ files.")

    async def operation(_run, _key, _kind, callback, **_kwargs):
        receipt = await callback()
        recorded.append(receipt)
        return receipt

    monkeypatch.setattr(design, "agent_tool_turn", turn)
    monkeypatch.setattr(design, "execute_tool", reject)
    monkeypatch.setattr(design, "operation", operation)
    result = await design.engineering_analysis(state(harness_version=4, research_mode=True))

    assert len(recorded) == 1
    assert recorded[0]["result"]["category"] == "engineering_tool"
    assert json.loads(result["engineering_history"][-1]["content"])["ok"] is False


@pytest.mark.asyncio
async def test_engineering_finish_returns_to_cad_with_current_evidence(monkeypatch, graph_mocks):
    async def turn(*args, **kwargs):
        return {"id":"finish", "name":"finish", "input":{"message":"Calculation remains unverified."}}, [], {"model_calls":3}
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    result = await design.engineering_analysis(state(engineering_from_coordinator=True))
    assert result["phase"] == "cad_session"
    assert result["engineering_summary"] == "Calculation remains unverified."
    assert result["model_calls"] == 3
    assert result["engineering_candidate_hash"]



def test_engineering_analysis_accepts_component_specific_material_choices():
    value = design.Analysis.model_validate({
        "summary": "Assembly material choices.",
        "selected_material": "Control arm: aluminum. " * 30,
        "manufacturing_method": "Mount: machined. Bushing: purchased. " * 25,
    })
    assert "Control arm" in value.selected_material
    assert "Bushing" in value.manufacturing_method


@pytest.mark.asyncio
async def test_reviewer_returns_measured_defect_to_cad(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {},
    }
    review = {"summary": "A gear intersects its housing.", "action": "repair", "findings": [{
        "id": "gear_overlap", "statement": "The output gear must clear the housing",
        "status": "observed_mismatch", "severity": "error",
        "evidence": ["intersection volume 1200 mm^3"],
        "explanation": "The exact imported STEP solids overlap.",
        "repair_instruction": "Cut a housing cavity around the output gear.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-1", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-1", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"identity": {}, "report": {"inspection": {"configurations": []}}, "artifacts": []},
    ))
    assert result["phase"] == "cad_session"
    assert result["review"]["action"] == "repair"
    assert "output gear" in result["cad_history"][-1]["content"]
    assert result["validation"]["report"]["review"]["findings"][0]["id"] == "gear_overlap"


@pytest.mark.asyncio
async def test_two_review_repairs_do_not_publish_failed_geometry(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }
    review = {"summary": "One overlap remains.", "action": "repair", "findings": [{
        "id": "overlap", "statement": "Parts should clear", "status": "observed_mismatch",
        "severity": "error", "evidence": ["10 mm3 overlap"], "explanation": "Measured overlap.",
        "repair_instruction": "Adjust the cavity.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-final", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-final", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"report": {"inspection": {}}}, review_repairs=2))
    assert result["phase"] == "cad_session"
    assert result["review"]["action"] == "repair"
    assert result["review"]["findings"]



@pytest.mark.asyncio
async def test_repeated_review_finding_remains_a_repair(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }
    review = {"summary": "The same overlap remains.", "action": "repair", "findings": [{
        "id": "gear_overlap", "statement": "Output gear intersects housing", "status": "observed_mismatch",
        "severity": "error", "evidence": ["10 mm3 overlap"], "explanation": "Measured overlap.",
        "repair_instruction": "Increase the housing cavity.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-repeat", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-repeat", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    state_value = state(validation={"report": {"inspection": {}}}, review_repairs=1,
        review_fingerprint=design.review_fingerprint(review), candidate_hash="new-candidate")
    result = await design.review_session(state_value)
    assert result["phase"] == "cad_session"
    assert result["review"]["action"] == "repair"
    assert result["review"]["findings"]



@pytest.mark.asyncio
async def test_review_repair_context_separates_passes_from_targets(graph_mocks):
    review = {"summary": "One issue.", "action": "repair", "findings": [
        {"id": "hole", "status": "observed_match", "statement": "Hole count correct"},
        {"id": "cavity", "status": "observed_mismatch", "statement": "Gear cavity missing",
            "evidence": ["The imported solids overlap by 10 mm3."],
            "repair_instruction": "Increase the housing cavity clearance."},
        {"id": "fatigue", "status": "requires_engineering", "statement": "Fatigue"},
    ]}
    result = await design.record_review_result(state(), review,
        {"manifest": {}, "files": {}}, {"report": {}}, [])
    repair_plan = json.loads(result["cad_history"][-1]["content"])
    assert [item["id"] for item in repair_plan["alreadyPassing"]] == ["hole"]
    assert [item["id"] for item in repair_plan["repairTargets"]] == ["cavity"]
    assert [item["id"] for item in repair_plan["unverifiedOrNonActionable"]] == ["fatigue"]


def test_review_finding_without_evidence_cannot_trigger_cad_repair():
    finding = {"id": "possible_gap", "status": "observed_mismatch",
        "statement": "A support may be missing", "evidence": [], "repair_instruction": "Add support."}
    assert not design.is_review_repair_target(finding)


@pytest.mark.asyncio
async def test_reviewer_allows_second_repair_for_a_different_finding(graph_mocks):
    review = {"summary": "A separate issue remains.", "action": "repair", "findings": [{
        "id": "missing_boss", "statement": "PCB boss is absent", "status": "not_observed",
        "severity": "warning", "evidence": ["No boss feature in source"],
        "explanation": "The PCB mounting boss is missing.", "repair_instruction": "Add the PCB boss.",
    }]}
    result = await design.record_review_result(
        state(review_repairs=1, review_fingerprint="different-prior-finding"), review,
        {"manifest": {}, "files": {}}, {"report": {}}, [])
    assert result["phase"] == "cad_session"
    assert result["review_repairs"] == 2


@pytest.mark.asyncio
async def test_reviewer_model_outage_does_not_block_publish(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }

    async def outage(*_args, **_kwargs):
        raise design.Pause("The review model is rate-limited.")

    monkeypatch.setattr(design, "agent_tool_turn", outage)
    result = await design.review_session(state(validation={"report": {"inspection": {}}}))
    assert result["phase"] == "publish"
    assert result["review"]["findings"][0]["id"] == "review_unavailable"
    assert "still published" in result["review"]["summary"]


@pytest.mark.asyncio
async def test_reviewer_preflight_rejects_uninspectable_assembly_inventory(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [{
            "id": "root", "name": "Root", "source": "assemblies/root.py", "kind": "assembly",
            "dependencies": [], "parameters": {}, "color": "#b9c4ad",
        }], "instances": [{"id": "root_inst", "definitionId": "root", "parentId": None,
            "name": "Root", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}}],
            "rootComponentId": "root", "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {"assemblies/root.py": "def build(p,d): return None"},
    }

    async def unexpected(*_args, **_kwargs):
        raise AssertionError("deterministic evidence gap must be handled before a model review")

    monkeypatch.setattr(design.models, "turn", unexpected)
    result = await design.review_session(state(validation={"report": {"inspection": {
        "components": {"root": {"solidCount": 3}},
        "configurations": [{"id": "as_built", "instanceCount": 0}],
    }}}))
    assert result["phase"] == "cad_session"
    assert result["review"]["findings"][0]["id"] == "assembly_instance_inventory"


@pytest.mark.asyncio
async def test_reviewer_keeps_reading_and_retrieval_available(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {},
    }
    review = {"summary": "Enough evidence gathered.", "action": "publish", "findings": []}

    async def turn(*_args, **kwargs):
        assert {'submit_review','read_file','read_project_source','read_project_records','measure_geometry'} <= {
            tool['function']['name'] for tool in _args[2]}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-budget", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-budget", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"report": {"inspection": {}}}, review_reads=3, review_inspected=True))
    assert result["phase"] == "publish"


@pytest.mark.asyncio
async def test_cad_session_suggests_tests_without_overriding_agent_action(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm",
            "components": [{"id": "block", "name": "Block", "source": "parts/block.py",
                "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
            "instances": [], "rootComponentId": "block", "references": [], "joints": [],
            "configurations": [], "featureOperations": []},
        "files": {"parts/block.py": "def build(p,d): return None"},
    }
    seen = {}
    async def turn(_config, messages, tools, **_kwargs):
        context = json.loads(messages[1]["content"].split(": ", 1)[1])
        assert context["testSuggested"]
        assert {"apply_changes", "read_file", "build"}.issubset({item["function"]["name"] for item in tools})
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "chosen-build", "type": "function", "function": {"name": "build", "arguments": '{"final":false}'}}]},
            "calls": [{"id": "chosen-build", "name": "build", "input": {"final": False}}],
            "inputTokens": 1, "outputTokens": 1}
    monkeypatch.setattr(design.models, "turn", turn)
    async def build(build_state):
        seen.update(build_state)
        return {"phase": "validate", "build_result": {"ok": True}}
    monkeypatch.setattr(design, "build", build)
    result = await design.cad_session(state(cad_edits_since_build=3))
    assert seen["pending_cad_call"]["name"] == "build"
    assert seen["pending_cad_call"]["id"] == "chosen-build"
    assert seen["cad_edits_since_build"] == 0
    assert result["phase"] == "validate"


def test_model_step_budget_bounds_each_tool_turn_not_the_whole_design():
    config = {"max_output_tokens": 64000}
    assert design.model_step_token_budget(config, "cad-session") == 64000
    assert design.model_step_token_budget(config, "coordinator-session") == 8192
    assert design.model_step_token_budget({"max_output_tokens": 4096}, "cad-session") == 4096
    assert Limits(maxModelCalls=96).maxModelCalls == 96


@pytest.mark.asyncio
async def test_incomplete_assembly_build_remains_an_intermediate_milestone(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm",
            "components": [
                {"id": "assembly", "name": "Assembly", "source": "assemblies/root.py",
                 "kind": "assembly", "dependencies": ["mount", "bushing"],
                 "parameters": {}, "color": "#b9c4ad"},
                {"id": "mount", "name": "Mount", "source": "parts/mount.py",
                 "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"},
                {"id": "bushing", "name": "Bushing", "source": "parts/bushing.py",
                 "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
            "instances": [], "rootComponentId": "assembly", "references": [],
            "joints": [], "configurations": [], "featureOperations": []},
        "files": {"assemblies/root.py": "def build(p,d): return None",
                  "parts/mount.py": "def build(p,d): return None",
                  "parts/bushing.py": "def build(p,d): return None"},
    }
    async def turn(_config, _messages, _tools, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "build-1", "type": "function",
            "function": {"name": "build", "arguments": '{"final":true}'}}]},
            "calls": [{"id": "build-1", "name": "build", "input": {"final": True}}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}
    async def capture_build(next_state):
        return {"phase": "validate", "build_final": next_state["build_final"],
                "requested_part_types": next_state["requested_part_types"],
                "built_part_types": next_state["built_part_types"]}
    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", capture_build)
    result = await design.cad_session(state(original_request=(
        "Design Task: suspension. Components (10 part types, ~22 instances).")))
    assert result["phase"] == "validate"
    assert result["build_final"] is False
    assert result["requested_part_types"] == 10
    assert result["built_part_types"] == 2


@pytest.mark.asyncio
async def test_successful_intermediate_build_returns_to_cad():
    result = await design.validate(state(
        build_result={"ok": True, "inspection": {}}, build_final=False,
        candidate_hash="partial-hash", pending_cad_call={"id": "build-1"},
    ))
    assert result["phase"] == "cad_session"
    assert result["last_milestone_hash"] == "partial-hash"
    assert result["pending_cad_call"] == {}


@pytest.mark.asyncio
async def test_full_build_failure_survives_source_edit_and_scopes_component_probe(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "drive_gear", "name": "Drive gear", "kind": "solid",
            "source": "parts/drive_gear.py"}], "rootComponentId": "drive_gear"},
        "files": {"parts/drive_gear.py": "def build(parameters, dependencies): return None"}}).model_dump()
    failure = {"ok": False, "attempt": 4, "error": {
        "category": "geometry", "fingerprint": "same-kernel-failure",
        "location": {"file": "parts/drive_gear.py", "line": 49},
        "diagnostic": "No rear face selected"}}
    first = await design.validate(state(run_id="run", build_result=failure, candidate_hash="candidate-1"))
    assert first["cad_last_build_failure"]["failureScope"] == "component"
    assert first["cad_last_build_failure"]["componentId"] == "drive_gear"
    assert first["cad_last_build_failure"]["repeatedCount"] == 1

    # A normal source edit clears ephemeral validation/build_result fields. The
    # durable repair target must still reach the next model decision.
    after_edit = {**state(run_id="run"), **first, "build_result": {}, "validation": {}}
    captured = {}

    async def turn(_state, **kwargs):
        captured.update(kwargs)
        return None, [], {"model_calls": 2}

    monkeypatch.setattr(design, "agent_tool_turn", turn)
    await design.cad_session_turn(after_edit)
    assert captured["context"]["activeFailure"]["file"] == "parts/drive_gear.py"
    assert captured["context"]["activeFailure"]["componentId"] == "drive_gear"
    probe = next(tool for tool in captured["tools"] if tool["function"]["name"] == "test_component")
    assert "enum" not in probe["function"]["parameters"]["properties"]["componentId"]

    repeated_failure_after_line_shift = {**failure, "error": {
        **failure["error"], "fingerprint": "same-kernel-failure-after-edit",
        "location": {"file": "parts/drive_gear.py", "line": 52},
    }}
    second = await design.validate({**after_edit, "build_result": repeated_failure_after_line_shift})
    assert second["cad_last_build_failure"]["repeatedCount"] == 2
    assert second["cad_last_build_failure"]["repeated"] is True
    after_second_edit = {**after_edit, **second, "build_result": {}}
    captured.clear()
    await design.cad_session_turn(after_second_edit)
    assert captured["context"]["activeFailure"]["repeatedCount"] == 2
    assert "test_component" in {tool["function"]["name"] for tool in captured["tools"]}

    # Even when a source edit produces a fresh error fingerprint, one focused
    # probe is enough for this active failed file until a complete build passes.
    after_first_probe = {**after_edit, "build_result": {},
        "cad_component_probe_failures": [design.failure_probe_key(first["cad_last_build_failure"])]}
    captured.clear()
    await design.cad_session_turn(after_first_probe)
    assert "test_component" in {tool["function"]["name"] for tool in captured["tools"]}


@pytest.mark.asyncio
async def test_assembly_build_failure_does_not_offer_isolated_component_probe(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "drive_gear", "name": "Drive gear", "kind": "solid",
                "source": "parts/drive_gear.py"},
            {"id": "pump", "name": "Pump", "kind": "assembly", "source": "assemblies/pump.py"}],
        "rootComponentId": "pump"}, "files": {"parts/drive_gear.py": "pass",
            "assemblies/pump.py": "pass"}}).model_dump()
    failure = {"ok": False, "error": {"category": "assembly_constraint",
        "location": {"file": "assemblies/pump.py", "line": 80}, "diagnostic": "Mate solve failed"}}
    stored = await design.validate(state(run_id="run", build_result=failure, candidate_hash="candidate-2"))
    assert stored["cad_last_build_failure"]["failureScope"] == "assembly"

    captured = {}
    async def turn(_state, **kwargs):
        captured.update(kwargs)
        return None, [], {"model_calls": 3}
    monkeypatch.setattr(design, "agent_tool_turn", turn)
    await design.cad_session_turn({**state(run_id="run"), **stored, "build_result": {}})
    assert captured["context"]["activeFailure"]["failureScope"] == "assembly"
    assert "test_component" in {tool["function"]["name"] for tool in captured["tools"]}


@pytest.mark.asyncio
async def test_saved_repair_sets_durable_full_build_gate(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "housing", "name": "Housing", "kind": "solid",
            "source": "parts/housing.py"}], "rootComponentId": "housing"},
        "files": {"parts/housing.py": "old source"}}).model_dump()
    failure = {"category": "geometry", "file": "parts/housing.py", "line": 12,
        "diagnostic": "boolean cut failed", "failureScope": "component", "componentId": "housing"}

    async def turn(_state, **_kwargs):
        call = {"id": "repair", "name": "apply_changes",
                "input": {"files": {"parts/housing.py": "def build(parameters, dependencies): return None"}}}
        message = {"role": "assistant", "content": "", "tool_calls": [{
            "id": call["id"], "type": "function", "function": {
                "name": call["name"], "arguments": json.dumps(call["input"])}}]}
        return call, [message], {"model_calls": 1, "search_count": 0}

    monkeypatch.setattr(design, "agent_tool_turn", turn)
    result = await design.cad_session_turn(state(run_id="run", original_request="Create a housing.",
        cad_last_build_failure=failure, cad_recovery_build_required=False,
        cad_edits_since_build=0))

    assert result["phase"] == "cad_session"
    assert result["cad_edits_since_build"] == 1
    assert result["cad_recovery_build_required"] is True


@pytest.mark.asyncio
async def test_active_build_failure_keeps_scoped_diagnostics_available(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = design.Snapshot.model_validate({"manifest": {
        "components": [
            {"id": "pump", "name": "Pump", "kind": "assembly", "source": "assemblies/pump.py",
             "dependencies": ["housing"]},
            {"id": "housing", "name": "Housing", "kind": "solid", "source": "parts/housing.py"}],
        "rootComponentId": "pump"},
        "files": {"assemblies/pump.py": "def build(parameters, dependencies): return None",
                  "parts/housing.py": "def build(parameters, dependencies): return None"}}).model_dump()
    failure = {"ok": False, "error": {"category": "assembly_constraint",
        "location": {"file": "assemblies/pump.py", "line": 80}, "diagnostic": "Mate solve failed"},
        "failureScope": "assembly", "file": "assemblies/pump.py"}
    captured = {}
    chosen = {"id": "premature-read", "name": "read_file", "input": {"path": "assemblies/pump.py"},
              "queued_calls": [{"id": "followup-edit", "name": "apply_changes"}]}

    async def turn(_state, **kwargs):
        captured.update(kwargs)
        message = {"role": "assistant", "content": "", "tool_calls": [{
            "id": chosen["id"], "type": "function", "function": {
                "name": chosen["name"], "arguments": json.dumps(chosen["input"])}}]}
        return chosen, [message], {"model_calls": 1, "search_count": 0}

    monkeypatch.setattr(design, "agent_tool_turn", turn)
    # Keep an explicit gate even if a graph transition resets the edit counter.
    current = state(run_id="run", cad_last_build_failure=failure, cad_edits_since_build=0,
                    cad_recovery_build_required=True)
    rejected = await design.cad_session_turn(current)

    offered = {tool["function"]["name"] for tool in captured["tools"]}
    assert {"read_file", "apply_changes", "test_component", "build"} <= offered
    assert "requiredBuildAfterRepair" not in captured["context"]
    feedback = json.loads(rejected["cad_history"][-1]["content"])
    assert feedback["content"] == graph_mocks["candidate"]["files"]["assemblies/pump.py"]



@pytest.mark.asyncio
async def test_component_probe_is_recorded_against_active_full_build_failure(monkeypatch):
    snapshot = design.Snapshot.model_validate({"manifest": {
        "components": [{"id": "drive_gear", "name": "Drive gear", "kind": "solid",
            "source": "parts/drive_gear.py"}], "rootComponentId": "drive_gear"},
        "files": {"parts/drive_gear.py": "def build(parameters, dependencies): return None"}}).model_dump()
    failure = {"category": "geometry", "file": "parts/drive_gear.py", "line": 49,
        "diagnostic": "No rear face selected", "failureScope": "component", "componentId": "drive_gear",
        "repeatedCount": 1}

    async def fake_run_row(_state):
        return {"id": "00000000-0000-0000-0000-000000000001"}

    async def fake_settings():
        return type("Settings", (), {"limits": type("Limits", (), {"maxRepairs": 3})()})()

    async def fake_build_candidate(_run, checkpoint, _limits, _key):
        return {"ok": True, "attempts": checkpoint["attempts"], "sandboxReady": True}

    async def fake_operation(_run, _key, _kind, execute, *, idempotent):
        assert idempotent is True
        return await execute()

    async def fake_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(design, "run_row", fake_run_row)
    monkeypatch.setattr(design, "app_settings", fake_settings)
    monkeypatch.setattr(design, "build_candidate", fake_build_candidate)
    monkeypatch.setattr(design, "operation", fake_operation)
    monkeypatch.setattr(design.repo, "event", fake_event)
    result = await design.probe_component(state(cad_last_build_failure=failure),
        snapshot, {"id": "probe", "name": "test_component", "input": {"componentId": "drive_gear"}},
        [], "drive_gear")

    assert result["cad_component_probe_failures"] == [design.failure_probe_key(failure)]
    assert design.failure_probe_key({**failure, "file": "assemblies/pump.py"}) == design.failure_probe_key(failure)


@pytest.mark.asyncio
@pytest.mark.parametrize("previous_final,requested_final,finalizes", [
    (False, True, True), (True, True, False), (False, False, False),
])
async def test_unchanged_successful_milestone_can_enter_final_review(
        monkeypatch, graph_mocks, previous_final, requested_final, finalizes):
    graph_mocks["candidate"] = {"manifest": {
        "components": [{"id": "plate", "kind": "solid"}],
        "rootComponentId": "plate"}, "files": {"parts/plate.py": "saved source"}}

    async def turn(*_args, **_kwargs):
        arguments = {"final": requested_final}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "final-build", "type": "function", "function": {
                "name": "build", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "final-build", "name": "build", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    build_states = []

    async def checked_build(build_state):
        build_states.append(build_state)
        return {"phase": "validate", "build_final": build_state["build_final"],
                "build_result": {"ok": True, "inspection": {}}}

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", checked_build)
    inputs = state(build_result={"ok": True}, build_final=previous_final,
                   cad_edits_since_build=0)
    result = await design.cad_session(inputs)
    if finalizes:
        assert len(build_states) == 1 and build_states[0]["build_final"] is True
        assert (await design.validate({**inputs, **result}))["phase"] == "review_session"
    else:
        assert build_states == [] and result["phase"] == "cad_session"
        feedback = json.loads(result["cad_history"][-1]["content"])
        assert feedback["category"] == "unchanged_successful_candidate"


@pytest.mark.asyncio
async def test_inline_build_persists_consumed_model_turn_and_milestone(monkeypatch, graph_mocks):
    async def build_candidate(_run, checkpoint, _limits, _key):
        checkpoint["attempts"] = 1
        return {"ok": True, "inspection": {}}

    monkeypatch.setattr(design, "build_candidate", build_candidate)
    output = await design.build(state(
        model_calls=7, search_count=1, build_final=False,
        requested_part_types=10, built_part_types=2,
        pending_cad_call={"id": "build-1"},
        cad_history=[{"role": "user", "content": "Stage two parts."}],
    ))
    assert output["phase"] == "validate"
    assert output["model_calls"] == 7
    assert output["search_count"] == 1
    assert output["build_final"] is False
    assert output["pending_cad_call"]["id"] == "build-1"
    assert output["built_part_types"] == 2

def test_numbered_component_inventory_for_incremental_assembly():
    request = """Design Task: mechanism
## Components (2 types)
1. Base plate — fixed structure.
2. Guide rail — two instances.

## Assembly Requirements
- Keep both rails aligned.
"""
    assert design.requested_component_labels(request) == ["Base plate", "Guide rail"]
    assert design.requested_component_labels("Create a single 80 x 50 mm plate.") == []


def test_quantity_marked_mechanical_inventory_counts_distinct_part_types():
    request = """Design Task: External Gear Pump
## Components (~27 instances)
Gear housing (1, die-cast Al), front cover (1, with seal bore), rear cover (1),
drive gear-shaft (1), driven gear-shaft (1), bushings (4), lip seal 16x28x7 (1),
O-rings ISO 3601 (2), M8x50 socket head screws ISO 4762 (6) + washers (6),
dowel pins ISO 2338 dia 6 (2), key DIN 6885 5x5x20 (1).

## Assembly Requirements
1. Both gears fit in the housing.
"""
    assert design.requested_component_labels(request) == [
        "Gear housing", "front cover", "rear cover", "drive gear-shaft",
        "driven gear-shaft", "bushings", "lip seal 16x28x7", "O-rings ISO 3601",
        "M8x50 socket head screws ISO 4762", "washers", "dowel pins ISO 2338 dia 6",
        "key DIN 6885 5x5x20",
    ]


@pytest.mark.asyncio
async def test_first_multi_component_cad_turn_exposes_incremental_milestone(monkeypatch, graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
Assembly Requirements:
- Keep both rails aligned.
"""
    manifest = {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base plate", "source": "parts/base.py",
            "kind": "solid", "dependencies": [], "parameters": {}, "color": "#b9c4ad"}],
        "instances": [], "rootComponentId": "base", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }
    arguments = {"files": [{"path": "parts/base.py",
        "content": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').box(10,10,2)"}],
        "manifest": manifest, "deletePaths": []}
    seen = {}

    async def turn(_config, messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        seen["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "base-patch", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "base-patch", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(original_request=request,
        cad_history=[{"role": "user", "content": request}]))
    assert "apply_changes" in seen["tools"]
    assert "read_file" not in seen["tools"]  # the first part has not been staged yet
    assert seen["context"]["componentMilestone"]["requestedTypes"] == ["Base plate", "Guide rail"]
    assert seen["context"]["request"] == request
    assert result["phase"] == "cad_session"
    assert len(graph_mocks["candidate"]["manifest"]["components"]) == 1


@pytest.mark.asyncio
async def test_cad_keeps_observation_tools_after_six_decisions(monkeypatch, graph_mocks):
    request = "Create a 10 mm cube and inspect the saved source."
    candidate = {"manifest": {
        "schemaVersion": 1, "units": "mm", "components": [{"id": "cube", "name": "Cube",
            "source": "parts/cube.py", "kind": "solid", "dependencies": [], "parameters": {}}],
        "instances": [], "rootComponentId": "cube", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }, "files": {"parts/cube.py": "def build(parameters, dependencies):\n    return None"}}
    graph_mocks["candidate"] = candidate
    current = state(original_request=request, cad_history=[{"role": "user", "content": request}])
    current["cad_observation_progress"] = {
        "epoch": observation_epoch(current, candidate), "seen": {}, "repeats": 0,
        "observationOnlyDecisions": design.MAX_CAD_OBSERVATION_DECISIONS,
    }
    seen = {}

    async def turn(_config, messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        seen["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        return {"message": {"role": "assistant", "content": ""}, "calls": [],
            "inputTokens": 1, "outputTokens": 1, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(current)

    assert result["phase"] == "cad_session"
    assert {"read_file", "search_files", "inspect_geometry", "diagnose_workspace"}.issubset(seen["tools"])
    assert {"apply_changes", "build", "test_component", "ask_user"}.issubset(seen["tools"])
    assert "observationLimit" not in seen["context"]


@pytest.mark.asyncio
async def test_three_distinct_source_reads_preserve_action_choice(monkeypatch, graph_mocks):
    request = "Build a cube and use its saved source to make a focused correction."
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm", "components": [{"id": "cube", "name": "Cube",
            "source": "parts/cube.py", "kind": "solid", "dependencies": [], "parameters": {}}],
        "instances": [], "rootComponentId": "cube", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }, "files": {"parts/cube.py": "def build(parameters, dependencies):\n    return None"}}
    graph_mocks["candidate"]["files"].update({f"parts/observation_{index}.py": f"value = {index}"
        for index in range(design.MAX_CAD_OBSERVATION_DECISIONS)})
    current = state(original_request=request,
        cad_history=[{"role": "user", "content": request}])
    decisions = []

    async def turn(_config, messages, tools, **_kwargs):
        names = [item["function"]["name"] for item in tools]
        decision = len(decisions)
        if decision < design.MAX_CAD_OBSERVATION_DECISIONS:
            name = "read_file"
            arguments = {"path": f"parts/observation_{decision}.py"}
        else:
            name = "apply_changes"
            arguments = {"files": [{"path": "parts/cube.py",
                "content": "def build(parameters, dependencies):\n    return 1"}],
                "manifest": None, "deletePaths": []}
        tool_call = {"id": f"decision-{decision}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(arguments)}}
        decisions.append({"tools": names, "action": name})
        return {"message": {"role": "assistant", "content": "", "tool_calls": [tool_call]},
            "calls": [{"id": tool_call["id"], "name": name, "input": arguments}],
            "inputTokens": 2, "outputTokens": 2, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    for _ in range(design.MAX_CAD_OBSERVATION_DECISIONS + 1):
        current.update(await design.cad_session(current))

    assert [item["action"] for item in decisions] == ["read_file"] * 3 + ["apply_changes"]
    assert "read_file" in decisions[-1]["tools"]
    assert "apply_changes" in decisions[-1]["tools"]
    assert graph_mocks["candidate"]["files"]["parts/cube.py"] == (
        "def build(parameters, dependencies):\n    return 1")


@pytest.mark.asyncio
async def test_second_part_requires_real_assembly_root_before_rebuild(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm", "rootComponentId": "base",
        "components": [{"id": "base", "kind": "solid"}, {"id": "gear", "kind": "solid"}],
        "instances": [{"id": "base_inst", "definitionId": "base"},
                      {"id": "gear_inst", "definitionId": "gear"}],
    }, "files": {"parts/base.py": "pass", "parts/gear.py": "pass"}}
    seen = {}

    async def turn(_config, messages, tools, **_kwargs):
        seen["tools"] = [item["function"]["name"] for item in tools]
        seen["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        return {"message": {"role": "assistant", "content": ""}, "calls": [],
                "inputTokens": 1, "outputTokens": 1, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(build_result={"ok": False,
        "error": {"category": "assembly_root_mismatch"}}))
    assert result["phase"] == "cad_session"
    assert {"apply_changes", "read_file", "build"}.issubset(seen["tools"])
    assert "rootComponentId" in seen["context"]["assemblyRootRepair"]


@pytest.mark.asyncio
async def test_partial_assembly_build_continues_until_all_types_staged(graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
Assembly Requirements:
- Keep both rails aligned.
"""
    graph_mocks["candidate"] = {"manifest": {"components": [
        {"id": "base", "kind": "solid"}]}, "files": {}}
    history = [{"role": "user", "content": request}, {"role": "assistant", "content": "",
        "tool_calls": [{"id": "build-1", "type": "function", "function": {
            "name": "build", "arguments": "{}"}}]}]
    inputs = state(original_request=request, cad_history=history,
        pending_cad_call={"id": "build-1"}, build_result={"ok": True})
    partial = await design.validate(inputs)
    assert partial["phase"] == "cad_session"
    assert "1 of 2" in partial["cad_history"][-1]["content"]
    graph_mocks["candidate"]["manifest"]["components"].append(
        {"id": "rail", "kind": "solid"})
    complete = await design.validate(inputs)
    assert complete["phase"] == "review_session"


@pytest.mark.asyncio
async def test_inline_build_keeps_model_call_ordinal_for_next_cad_turn(monkeypatch, graph_mocks):
    request = """Design Task: mechanism
Components:
1. Base plate — fixed structure.
2. Guide rail — two instances.
"""
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base plate", "kind": "solid"}],
        "instances": [], "rootComponentId": "base", "references": [], "joints": [],
        "configurations": [], "featureOperations": [],
    }, "files": {"parts/base.py": "def build(p,d): pass"}}
    arguments = {}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "build-2", "type": "function", "function": {
                "name": "build", "arguments": "{}"}}]},
            "calls": [{"id": "build-2", "name": "build", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    seen = {}

    async def inline_build(build_state):
        seen["model_calls"] = build_state["model_calls"]
        return {"phase": "validate", "attempts": 2}

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", inline_build)
    result = await design.cad_session(state(original_request=request, model_calls=4,
        cad_edits_since_build=1, cad_history=[{"role": "user", "content": request}]))

    assert seen["model_calls"] == 5
    assert result["phase"] == "validate"
    assert result["model_calls"] == 5
    assert result["cad_edits_since_build"] == 0
    assert result["pending_cad_call"]["id"] == "build-2"


@pytest.mark.asyncio
async def test_build_ledger_key_changes_with_candidate_and_requirements(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {"components": [{"id": "base", "kind": "solid"}],
        "rootComponentId": "base"}, "files": {"parts/base.py": "source version 1"}}
    keys = []

    async def capture_operation(_run, key, _kind, callback, **_kwargs):
        keys.append(key)
        return await callback()

    async def fake_build(_run, cp, _limits, _key):
        cp["attempts"] += 1
        return {"ok": True}

    monkeypatch.setattr(design, "operation", capture_operation)
    monkeypatch.setattr(design, "build_candidate", fake_build)
    await design.build(state(attempts=2))
    await design.build(state(attempts=2))
    graph_mocks["candidate"]["files"]["parts/base.py"] = "source version 2"
    await design.build(state(attempts=2))
    await design.build(state(attempts=2, requirements=[{"id": "new-check",
        "kind": "unverified", "description": "Additional advisory check"}]))

    assert keys[0] == keys[1]
    assert keys[1] != keys[2]
    assert keys[2] != keys[3]


@pytest.mark.asyncio
async def test_cad_session_registers_buildable_part_when_manifest_is_omitted(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {"manifest": {
        "schemaVersion": 1, "units": "mm",
        "components": [{"id": "base", "name": "Base", "source": "parts/base.py",
            "kind": "solid", "dependencies": [], "parameters": {}}],
        "instances": [], "rootComponentId": "base",
    }, "files": {"parts/base.py": "def build(parameters, dependencies):\n    return None"}}
    arguments = {"files": [{"path": "parts/sector_gear.py",
        "content": "def build(parameters, dependencies):\n    return None"}],
        "manifest": None, "deletePaths": []}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "new-part", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "new-part", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state())
    components = graph_mocks["candidate"]["manifest"]["components"]
    assert [(item["id"], item["source"]) for item in components] == [
        ("base", "parts/base.py"), ("sector_gear", "parts/sector_gear.py")]
    assert "sector_gear" in result["cad_history"][-1]["content"]
    assert graph_mocks["candidate"]["manifest"]["rootComponentId"] == "base"


def test_auto_registration_skips_helpers_without_build_and_existing_components():
    manifest = {"components": [{"id": "base", "source": "parts/base.py", "kind": "solid"}]}
    files = {
        "parts/base.py": "def build(parameters, dependencies):\n    return None",
        "parts/helper.py": "def radius():\n    return 1",
        "parts/rail.py": "def build(parameters, dependencies):\n    return None",
    }
    updated, added = design.register_unlisted_part_sources(manifest, files)
    assert added == ["rail"]
    assert len(updated["components"]) == 2
    assert len(manifest["components"]) == 1


def test_auto_registration_repairs_matching_omitted_assembly_root_definition():
    manifest = {"components": [{"id": "housing", "name": "Housing",
        "source": "parts/housing.py", "kind": "solid", "dependencies": [],
        "parameters": {}, "color": "#b9c4ad"}],
        "instances": [{"id": "external_gear_pump", "definitionId": "external_gear_pump",
            "name": "External Gear Pump", "frame": {"position": [0, 0, 0],
            "rotation": [0, 0, 0]}}], "rootComponentId": "external_gear_pump"}
    files = {"parts/housing.py": "def build(parameters, dependencies):\n    return None",
        "assemblies/external_gear_pump.py":
            "def build(parameters, dependencies):\n    return dependencies['housing']"}

    updated, added = design.register_unlisted_part_sources(manifest, files)
    candidate = design.Snapshot.model_validate({"manifest": updated, "files": files})

    assert added == ["external_gear_pump"]
    root = next(item for item in candidate.manifest.components
                if item.id == "external_gear_pump")
    assert root.kind == "assembly"
    assert root.source == "assemblies/external_gear_pump.py"
    assert root.dependencies == ["housing"]
    assert candidate.manifest.instances[0].definitionId == root.id


def test_auto_registration_does_not_infer_unmatched_assembly_root():
    manifest = {"components": [], "rootComponentId": "pump"}
    updated, added = design.register_unlisted_part_sources(manifest, {
        "assemblies/other.py": "def build(parameters, dependencies):\n    return None"})
    assert added == []
    assert updated["components"] == []


@pytest.mark.asyncio
async def test_cad_session_stops_repeated_text_instead_of_tool_call(monkeypatch, graph_mocks):
    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "[[{invalid tool text]"},
            "calls": [], "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    first = await design.cad_session(state())
    assert first["phase"] == "cad_session"
    assert first["cad_invalid_tool_attempts"] == 1
    stopped = await design.cad_session(state(cad_invalid_tool_attempts=2))
    assert stopped["phase"] == "cad_recovery"
    assert "terminal_status" not in stopped
    assert "no usable tool call three times" in stopped["final_message"]


@pytest.mark.asyncio
async def test_cad_session_stops_repeated_malformed_tool_arguments(monkeypatch, graph_mocks):
    arguments = {"path": ""}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "empty-path", "type": "function", "function": {
                "name": "read_file", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "empty-path", "name": "read_file", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.cad_session(state(cad_invalid_tool_attempts=2))
    assert result["phase"] == "cad_recovery"
    assert "terminal_status" not in result
    assert "invalid tool action three times" in result["final_message"]


@pytest.mark.asyncio
async def test_manifest_repair_survives_history_loss_and_accepts_atomic_fix(monkeypatch, graph_mocks):
    original = design.Snapshot.model_validate({"manifest": {"components": [
        {"id": "housing", "name": "Housing", "kind": "solid", "source": "parts/housing.py"}],
        "rootComponentId": "housing"},
        "files": {"parts/housing.py": "def build(parameters, dependencies):\n    return None"}}).model_dump()
    graph_mocks["candidate"] = deepcopy(original)
    manifest = deepcopy(original["manifest"])
    manifest["rootComponentId"] = "pump"
    arguments = {"manifest": manifest, "files": {}}
    contexts = []
    events = []

    async def event(_run, message, **kwargs):
        events.append((message, kwargs))

    async def turn(_config, messages, tools, **_kwargs):
        contexts.append((json.loads(messages[1]["content"].split(": ", 1)[1]), tools))
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "repair", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "repair", "name": "apply_changes", "input": deepcopy(arguments)}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design.repo, "event", event)
    current = state()
    first = await design.cad_session(current)
    assert graph_mocks["candidate"] == original
    assert first["cad_contract_repair"]["issues"][0]["code"] == "unknown_root"
    # Simulate rolling history compaction/checkpoint recovery. Dedicated repair
    # state still carries the exact blockers to the next model call.
    current.update(first)
    current["cad_history"] = []
    second = await design.cad_session(current)
    assert second["cad_contract_repair"]["stalledAttempts"] == 2
    assert contexts[-1][0]["workspaceRepair"]["issues"]
    assert {"apply_changes", "read_file", "search_files", "ask_user"} <= {tool["function"]["name"] for tool in contexts[-1][1]}
    manifest["components"].append({"id": "pump", "name": "Pump", "kind": "assembly",
                                   "source": "assemblies/pump.py", "dependencies": ["housing"]})
    current.update(second)
    current["cad_history"] = []
    third = await design.cad_session(current)
    assert third["phase"] == "cad_session"
    assert third["cad_contract_repair"]["stalledAttempts"] == 1
    assert third["cad_contract_repair"]["issues"][0]["code"] == "missing_source"
    assert graph_mocks["candidate"] == original
    arguments["files"] = {"assemblies/pump.py": "def build(parameters, dependencies):\n    return None"}
    current.update(third)
    fixed = await design.cad_session(current)
    assert fixed["cad_contract_repair"] == {}
    assert fixed["cad_invalid_tool_attempts"] == 0
    assert graph_mocks["candidate"]["manifest"]["rootComponentId"] == "pump"
    assert fixed["validation"] == {}  # staging a contract is not geometry validation
    assert any("unknown_root at rootComponentId" in message for message, _ in events)
    assert all("return None" not in message for message, _ in events)


@pytest.mark.asyncio
async def test_stalled_manifest_repair_preserves_candidate_and_reports_blocker(monkeypatch, graph_mocks):
    arguments = {"files": {}, "manifest": {"rootComponentId": "missing"}}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "invalid", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "invalid", "name": "apply_changes", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    current = state()
    for _ in range(3):
        result = await design.cad_session(current)
        current.update(result)
        current["cad_history"] = []
    assert result["phase"] == "cad_recovery"
    assert "terminal_status" not in result
    assert "missing has no component definition" in result["final_message"]
    assert "candidate" not in graph_mocks


@pytest.mark.asyncio
async def test_pending_contract_repair_cannot_be_bypassed_by_build(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = design.Snapshot.model_validate({"manifest": {"components": [
        {"id": "base", "name": "Base", "kind": "solid", "source": "parts/base.py"}],
        "rootComponentId": "base"},
        "files": {"parts/base.py": "def build(parameters, dependencies):\n    return None"}}).model_dump()

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "ignored-repair", "type": "function", "function": {
                "name": "build", "arguments": "{}"}}]},
            "calls": [{"id": "ignored-repair", "name": "build", "input": {}}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    async def unexpected_build(*_args, **_kwargs):
        pytest.fail("Rejected edits must be repaired before any automatic or provider-selected build")

    monkeypatch.setattr(design.models, "turn", turn)
    monkeypatch.setattr(design, "build", unexpected_build)
    result = await design.cad_session(state(cad_edits_since_build=3,
        cad_contract_repair={"issues": [{"code": "unknown_root", "path": "rootComponentId"}],
                             "stalledAttempts": 1}))
    assert result["phase"] == "cad_session"
    assert "Repair the rejected edit" in result["cad_history"][-1]["content"]


@pytest.mark.asyncio
async def test_coordinator_inspection_cache_survives_history_loss_and_context_changes(monkeypatch, graph_mocks):
    contexts = []

    async def turn(_config, messages, tools, **_kwargs):
        contexts.append((json.loads(messages[1]["content"].split(": ", 1)[1]), tools))
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "inspect", "type": "function", "function": {"name": "inspect_project", "arguments": "{}"}}]},
            "calls": [{"id": "inspect", "name": "inspect_project", "input": {}}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    current = state(project_context={"revision": None, "previousMessages": []})
    first = await design.coordinator_session(current)
    current.update(first)
    current["coordinator_history"] = []
    second = await design.coordinator_session(current)
    assert "inspect_project" not in {t["function"]["name"] for t in contexts[-1][1]}
    assert "already inspected" in contexts[-1][0]["projectInspection"]
    assert json.loads(second["coordinator_history"][-1]["content"])["category"] == "unavailable_tool"
    current.update(second)
    current["project_context"] = {"revision": None, "previousMessages": [{"role": "user", "content": "New evidence"}]}
    third = await design.coordinator_session(current)
    assert "inspect_project" in {t["function"]["name"] for t in contexts[-1][1]}
    assert third["coordinator_inspected_context"] != first["coordinator_inspected_context"]


@pytest.mark.asyncio
async def test_coordinator_does_not_offer_revision_restore_for_an_empty_project(monkeypatch, graph_mocks):
    observed = {}
    arguments = {"role": "cad", "task": "Build the requested assembly.", "requirements": []}

    async def turn(_config, messages, tools, **_kwargs):
        observed["context"] = json.loads(messages[1]["content"].split(": ", 1)[1])
        observed["tools"] = {item["function"]["name"] for item in tools}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate", "type": "function", "function": {
                "name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate", "name": "delegate", "input": deepcopy(arguments)}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session(state(project_context={
        "revision": None, "recentRevisions": [], "previousMessages": []}))

    assert "restore_revision" not in observed["tools"]
    assert observed["context"]["restorableRevisionIds"] == []
    assert result["phase"] == "cad_session"

    await design.coordinator_session(state(project_context={"revision": None,
        "recentRevisions": [{"id": "existing-revision", "ordinal": 1}], "previousMessages": []}))
    assert "restore_revision" in observed["tools"]
    assert observed["context"]["restorableRevisionIds"] == ["existing-revision"]


@pytest.mark.asyncio
async def test_invalid_revision_restore_returns_feedback_without_pausing(monkeypatch, graph_mocks):
    arguments = {"summary": "Restore previous assembly", "revisionId": "not-a-uuid"}

    async def turn(_config, _messages, _tools, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "restore", "type": "function", "function": {
                "name": "restore_revision", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "restore", "name": "restore_revision", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session(state(project_context={"revision": None,
        "recentRevisions": [{"id": "existing-revision", "ordinal": 1}], "previousMessages": []}))

    assert result["phase"] == "coordinator_session"
    assert result["coordinator_contract_repair"]["category"] == "unknown_revision"
    assert result["coordinator_contract_repair"]["availableRevisionIds"] == ["existing-revision"]


@pytest.mark.asyncio
async def test_coordinator_repairs_single_dimension_without_inventing_axes(monkeypatch, graph_mocks):
    arguments = {"role": "cad", "task": "Build the requested pump.", "requirements": [{
        "id": "spacing", "description": "Shaft centre separation 34 mm", "kind": "dimensions",
        "dimensions": [34], "tolerance": .01}]}
    contexts = []

    async def turn(_config, messages, _tools, **_kwargs):
        contexts.append(json.loads(messages[1]["content"].split(": ", 1)[1]))
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate", "type": "function", "function": {
                "name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate", "name": "delegate", "input": deepcopy(arguments)}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    current = state(original_request="Create a pump with shaft centre separation 34 mm.", coordinator_actions=10)
    first = await design.coordinator_session(current)
    assert first["phase"] == "coordinator_session"
    assert [i["path"] for i in first["coordinator_contract_repair"]["issues"]] == [
        "requirements.0.dimensions.1", "requirements.0.dimensions.2"]
    assert "invented values" in first["coordinator_contract_repair"]["repairGuidance"]
    assert "input_value" not in json.dumps(first["coordinator_contract_repair"])
    current.update(first)
    current["coordinator_history"] = []
    arguments["requirements"][0].update(kind="unverified", dimensions=None)
    fixed = await design.coordinator_session(current)
    assert contexts[-1]["toolRepair"]["issues"]
    assert fixed["phase"] == "cad_session"
    assert fixed["coordinator_contract_repair"] == {}
    assert fixed["coordinator_actions"] == 0
    assert fixed["model_calls"] == 2  # delegation does not reset the global call budget
    requirement = next(r for r in fixed["requirements"] if r["id"] == "spacing")
    assert requirement["kind"] == "unverified"
    assert requirement["dimensions"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("session", ["coordinator", "cad"])
async def test_empty_workspace_does_not_offer_reads_of_nonexistent_files(monkeypatch, graph_mocks, session):
    observed = []

    async def turn(_config, _messages, tools, **_kwargs):
        observed.extend(t["function"]["name"] for t in tools)
        return {"message": {"role": "assistant", "content": ""}, "calls": [],
                "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    await (design.coordinator_session(state()) if session == "coordinator" else design.cad_session(state()))
    assert not {"read_file", "search_files", "inspect_geometry"}.intersection(observed)
    assert ("delegate" if session == "coordinator" else "apply_changes") in observed


@pytest.mark.asyncio
async def test_second_reviewer_repair_preserves_failed_requirement(monkeypatch, graph_mocks):
    graph_mocks["candidate"] = {
        "manifest": {"schemaVersion": 1, "units": "mm", "components": [], "instances": [],
            "rootComponentId": None, "references": [], "joints": [],
            "configurations": [], "featureOperations": []}, "files": {},
    }
    review = {"summary": "One overlap remains.", "action": "repair", "findings": [{
        "id": "overlap", "statement": "Parts should clear", "status": "observed_mismatch",
        "severity": "error", "evidence": ["10 mm3 overlap"], "explanation": "Measured overlap.",
        "repair_instruction": "Adjust the cavity.",
    }]}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "review-final", "type": "function", "function": {
                "name": "submit_review", "arguments": json.dumps(review)}}]},
            "calls": [{"id": "review-final", "name": "submit_review", "input": review}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.review_session(state(
        validation={"report": {"inspection": {}}}, review_repairs=2))
    assert result["phase"] == "cad_session"
    assert result["review"]["action"] == "repair"
    assert result["review"]["findings"]



def test_engineering_material_and_process_can_be_explanatory_text():
    result = design.Analysis(
        summary="Preliminary engineering assessment.",
        selected_material="7075-T6 with certified minimum yield, density and temper-specific properties " * 12,
        manufacturing_method="Forged case and carrier, finish-machined at the bearing bores and mounting faces " * 12,
    )
    assert len(result.selected_material) > 200
    assert len(result.manufacturing_method) > 200


@pytest.mark.asyncio
async def test_pending_contract_correction_resumes_without_replaying_primary(monkeypatch, graph_mocks):
    diagnostic = "selected_material and manufacturing_method exceeded their field limits"
    rows = {
        "graph:analysis:0": {"status": "failed", "result": {
            "category": "tool_protocol", "diagnostic": diagnostic}},
        "graph:analysis:contract-repair:1": {"status": "ambiguous", "result": None},
    }

    async def one(_table, params, *, required=True):
        key = params["operation_key"].removeprefix("eq.")
        return rows.get(key)

    monkeypatch.setattr(design.db, "one", one)
    calls = []
    payload = {"summary": "Conservative concept assessment.",
               "selected_material": "7075-T6", "manufacturing_method": "CNC machining"}

    async def turn(_config, messages, tools, **_kwargs):
        calls.append(messages)
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "analysis-correction", "type": "function", "function": {
                "name": "submit_analysis", "arguments": json.dumps(payload)}}]},
            "calls": [{"id": "analysis-correction", "name": "submit_analysis", "input": payload}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    value, usage = await design.structured_turn(
        state(), "engineering", "analysis", "Assess the design.", "submit_analysis", design.Analysis)

    assert value.selected_material == "7075-T6"
    assert usage["model_calls"] == 2
    assert len(calls) == 1  # Only the correction call; the primary call is not replayed.
    assert diagnostic in calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_resumed_coordinator_answer_reaches_context_and_cad_delegation(monkeypatch, graph_mocks):
    original = "Ask me for the new plate thickness before editing."
    answer = "Use 3 mm and preserve all 60 occurrences."
    monkeypatch.setattr(design, "interrupt", lambda _payload: {"message": answer})
    initial = state(original_request=original, question="New thickness?",
        coordinator_pending_call={"id": "question", "name": "ask_user"},
        coordinator_history=[{"role": "user", "content": original},
            {"role": "assistant", "tool_calls": [{"id": "question", "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question":"New thickness?"}'}}]}])
    resumed = await design.coordinator_question(initial)
    assert resumed["coordinator_history"][-1] == {"role": "user", "content": answer}

    async def turn(_config, messages, _tools, **_kwargs):
        context = json.loads(messages[1]["content"].split(": ", 1)[1])
        assert answer in context["latestRequest"]
        assert messages[-1] == {"role": "user", "content": answer}
        arguments = {"role": "cad", "task": "Set thickness to 3 mm."}
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "delegate", "type": "function", "function": {"name": "delegate", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "delegate", "name": "delegate", "input": arguments}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    result = await design.coordinator_session({**initial, **resumed})
    assert result["phase"] == "cad_session"
    task = json.loads(result["cad_history"][0]["content"])
    assert answer in task["latestRequest"]
    assert initial["original_request"] == original


@pytest.mark.asyncio
async def test_resumed_cad_answer_is_presented_as_user_input(monkeypatch, graph_mocks):
    original = "Ask me for the thickness."
    answer = "The thickness is 3 mm."
    monkeypatch.setattr(design, "interrupt", lambda _payload: {"message": answer})
    initial = state(original_request=original, pending_cad_call={"id": "question", "name": "ask_user"},
        cad_history=[{"role": "user", "content": original},
            {"role": "assistant", "tool_calls": [{"id": "question", "type": "function",
                "function": {"name": "ask_user", "arguments": '{"question":"Thickness?"}'}}]}])
    resumed = await design.cad_question(initial)
    assert resumed["cad_history"][-1] == {"role": "user", "content": answer}

    async def tool_turn(_state, **kwargs):
        assert kwargs["context"]["request"] == original
        assert answer in [item["message"] for item in kwargs["context"]["humanUpdates"]]
        assert kwargs["history"][-1] == {"role": "user", "content": answer}
        return None, kwargs["history"], {"model_calls": 1, "search_count": 0}

    monkeypatch.setattr(design, "agent_tool_turn", tool_turn)
    await design.cad_session({**initial, **resumed})


def test_assumption_keywords_do_not_grant_blanket_permission():
    current = state(original_request="Design the housing and gears.",
        clarified_request=("Design the housing and gears.\n\nUser clarification: "
            "Mark unspecified dimensions unverified or state any necessary modeling assumption explicitly."),
        project_context={"authoritativeInputs": {"originalBrief": "Use a 34 mm center distance.",
            "humanCorrections": []}})
    answer = design.permitted_assumption_answer(current, "What is the cover thickness and bolt spacing?")
    assert answer is None  # Keyword permission cannot authorize an unrelated missing decision.



def test_pump_wording_does_not_authorize_every_missing_choice():
    current = state(original_request="Build the external gear pump.",
        clarified_request=("Build the external gear pump. User clarification: The housing footprint, "
            "cover thicknesses, bolt and dowel positions, and exact O-ring size are not specified. "
            "Do not present missing values as supplied facts: mark them as assumptions/unverified "
            "or make explicit modeling assumptions and label them."))
    answer = design.permitted_assumption_answer(current,
        "Please provide the housing footprint, cover thicknesses, bolt locations and exact O-ring size.")
    assert answer is None  # Keyword permission cannot authorize an unrelated missing decision.



def test_clarification_remains_available_without_user_assumption_permission():
    current = state(original_request="Design a mounting bracket.", clarified_request="Design a mounting bracket.")
    assert design.permitted_assumption_answer(current, "What is the mounting hole spacing?") is None


@pytest.mark.asyncio
async def test_coordinator_question_is_not_automatically_answered_by_keyword(monkeypatch, graph_mocks):
    arguments = {"question": "What are the housing footprint and cover thicknesses?"}

    async def turn(_config, _messages, _tools, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "question", "type": "function", "function": {
                "name": "ask_user", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "question", "name": "ask_user", "input": arguments}],
            "inputTokens": 1, "outputTokens": 1, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    current = state(original_request="Build an external gear pump.",
        clarified_request=("Build an external gear pump. User clarification: Mark missing dimensions "
            "unverified or state any necessary modeling assumption explicitly."),
        project_context={"revision": None, "recentRevisions": [], "previousMessages": [],
            "authoritativeInputs": {"originalBrief": "Gear module 2.0 mm.", "humanCorrections": []}})
    result = await design.coordinator_session(current)
    assert result["phase"] == "coordinator_question"
    assert result["question"] == arguments["question"]



@pytest.mark.asyncio
async def test_source_signature_regression_is_atomic_and_repair_survives_history_loss(monkeypatch, graph_mocks):
    original = design.Snapshot.model_validate({"manifest": {"components": [
        {"id": "cover", "name": "Cover", "kind": "solid", "source": "parts/cover.py"}],
        "rootComponentId": "cover"}, "files": {
            "parts/cover.py": "def build(parameters, dependencies):\n    return None"}}).model_dump()
    graph_mocks["candidate"] = deepcopy(original)
    arguments = {"files": {"parts/cover.py": "def build(params):\n    return 'PRIVATE_CODE'"}}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "repair-signature", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "repair-signature", "name": "apply_changes", "input": deepcopy(arguments)}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    current = state()
    rejected = await design.cad_session(current)
    assert graph_mocks["candidate"] == original
    issue = rejected["cad_contract_repair"]["issues"][0]
    assert issue["code"] == "build_signature"
    assert issue["path"] == "parts/cover.py"
    assert "PRIVATE_CODE" not in json.dumps(rejected["cad_contract_repair"])
    current.update(rejected)
    current["cad_history"] = []
    arguments["files"]["parts/cover.py"] = "def build(parameters, dependencies):\n    return None"
    accepted = await design.cad_session(current)
    assert accepted["cad_contract_repair"] == {}
    assert graph_mocks["candidate"] == original
    assert "validation" not in accepted  # Unchanged valid source keeps any current evidence.


@pytest.mark.asyncio
async def test_entrypoint_preflight_allows_incremental_repairs_with_unrelated_legacy_source(monkeypatch, graph_mocks):
    original = design.Snapshot.model_validate({"manifest": {"components": [
        {"id": "cover", "name": "Cover", "kind": "solid", "source": "parts/cover.py"},
        {"id": "legacy", "name": "Legacy", "kind": "solid", "source": "parts/legacy.py"}],
        "rootComponentId": "cover"}, "files": {
            "parts/cover.py": "def build(parameters, dependencies):\n    return None",
            "parts/legacy.py": "def build(params):\n    return None"}}).model_dump()
    graph_mocks["candidate"] = deepcopy(original)
    arguments = {"files": {"parts/cover.py": "def build(parameters, dependencies):\n    pass"}}

    async def turn(*_args, **_kwargs):
        return {"message": {"role": "assistant", "content": "", "tool_calls": [{
            "id": "focused-edit", "type": "function", "function": {
                "name": "apply_changes", "arguments": json.dumps(arguments)}}]},
            "calls": [{"id": "focused-edit", "name": "apply_changes", "input": deepcopy(arguments)}],
            "inputTokens": 10, "outputTokens": 20, "webSearchRequests": 0}

    monkeypatch.setattr(design.models, "turn", turn)
    accepted = await design.cad_session(state())
    assert accepted["cad_contract_repair"] == {}
    assert graph_mocks["candidate"]["files"]["parts/cover.py"] == arguments["files"]["parts/cover.py"]
    assert graph_mocks["candidate"]["files"]["parts/legacy.py"] == original["files"]["parts/legacy.py"]
    assert accepted["validation"] == {}  # runtime still must qualify the whole candidate
