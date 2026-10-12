import json
from copy import deepcopy

import pytest

from forma_api.agent_memory import notes_just_saved,older_outcomes_available
from forma_api.graphs import design


def saved_notes():
    return [{'role':'assistant','tool_calls':[{'id':'notes','type':'function',
        'function':{'name':'update_working_notes','arguments':'{"nextAction":"Edit source"}'}}]},
        {'role':'tool','tool_call_id':'notes','content':'{"ok":true,"agentReportedNotes":true}'}]


@pytest.mark.asyncio
@pytest.mark.parametrize('role',['cad','coordinator'])
async def test_successful_notes_offer_a_different_action_and_renew_after_it(monkeypatch,role):
    snapshot={'manifest':{'components':[],'instances':[],'rootComponentId':None},'files':{}}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service,'load_candidate',load)
    history_key=role+'_history'
    state={'run_id':'run','original_request':'Build an arbitrary part',history_key:saved_notes(),
        'working_memory':{'notes':{'pendingRequirements':['Exact user values']}}}
    seen=[]
    async def turn(_state,**kwargs):
        seen.append({item['function']['name'] for item in kwargs['tools']})
        call={'id':'read','name':'diagnose_workspace','input':{}}
        history=[*kwargs['history'],{'role':'assistant','tool_calls':[{'id':'read','type':'function',
            'function':{'name':'diagnose_workspace','arguments':'{}'}}]}]
        return call,history,{'model_calls':1}
    monkeypatch.setattr(design,'agent_tool_turn',turn)
    node=design.cad_session if role=='cad' else design.coordinator_session
    result=await node(state)
    assert 'update_working_notes' not in seen[0] and 'diagnose_workspace' in seen[0]
    assert 'apply_changes' in seen[0] if role=='cad' else 'delegate' in seen[0]
    assert result['phase']==role+'_session'
    state.update(result)
    await node(state)
    assert 'update_working_notes' in seen[1]
    assert snapshot['files']=={} and state['working_memory']['notes']['pendingRequirements']==['Exact user values']


@pytest.mark.asyncio
async def test_provider_cannot_execute_omitted_note_action_or_reset_usage(monkeypatch):
    snapshot={'manifest':{'components':[],'instances':[],'rootComponentId':None},'files':{}}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service,'load_candidate',load)
    memory={'notes':{'pendingRequirements':['Preserve every requested feature']}}
    async def turn(_state,**kwargs):
        assert 'update_working_notes' not in {item['function']['name'] for item in kwargs['tools']}
        call={'id':'repeat','name':'update_working_notes','input':{'pendingRequirements':[]}}
        history=[*kwargs['history'],{'role':'assistant','tool_calls':[{'id':'repeat','type':'function',
            'function':{'name':call['name'],'arguments':json.dumps(call['input'])}}]}]
        return call,history,{'model_calls':19}
    monkeypatch.setattr(design,'agent_tool_turn',turn)
    result=await design.cad_session({'run_id':'run','original_request':'Keep all features',
        'cad_history':saved_notes(),'working_memory':deepcopy(memory),'model_calls':18,
        'cad_invalid_tool_attempts':2,'attempts':4})
    assert result['phase']=='cad_recovery' and result['model_calls']==19
    assert 'attempts' not in result and 'working_memory' not in result
    assert json.loads(result['cad_history'][-1]['content'])['category']=='note_progress'
    assert notes_just_saved(result['cad_history'])
    assert memory['notes']['pendingRequirements']==['Preserve every requested feature']


def test_failed_note_and_new_human_input_do_not_suppress_required_note_updates():
    failed=saved_notes();failed[-1]['content']='{"ok":false}'
    assert not notes_just_saved(failed)
    assert not notes_just_saved([*saved_notes(),{'role':'user','content':'A new design decision'}])


@pytest.mark.asyncio
@pytest.mark.parametrize('role',['cad','coordinator'])
@pytest.mark.parametrize('count',[0,12,13])
async def test_memory_retrieval_is_offered_only_for_facts_outside_current_context(monkeypatch,role,count):
    snapshot={'manifest':{'components':[],'instances':[],'rootComponentId':None},'files':{}}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service,'load_candidate',load)
    async def turn(_state,**kwargs):
        names={item['function']['name'] for item in kwargs['tools']}
        assert ('read_working_memory' in names)==(count>12)
        if count<=12: assert 'already included' in kwargs['context']['memoryAvailability']
        call={'id':'diagnose','name':'diagnose_workspace','input':{}}
        history=[{'role':'assistant','tool_calls':[{'id':'diagnose','type':'function',
            'function':{'name':call['name'],'arguments':'{}'}}]}]
        return call,history,{'model_calls':2}
    monkeypatch.setattr(design,'agent_tool_turn',turn)
    memory={'outcomes':{str(i):{'guidance':'Existing fact'} for i in range(count)},
        'notes':{'pendingRequirements':['Keep exact values']}}
    node=design.cad_session if role=='cad' else design.coordinator_session
    result=await node({'run_id':'run','original_request':'Create an arbitrary part','working_memory':memory})
    assert result['phase']==role+'_session' and memory['notes']['pendingRequirements']==['Keep exact values']
    assert snapshot['files']=={}


@pytest.mark.asyncio
async def test_ignored_empty_memory_read_is_not_executed_and_cannot_renew_notes(monkeypatch):
    snapshot={'manifest':{'components':[],'instances':[],'rootComponentId':None},'files':{}}
    async def load(_): return deepcopy(snapshot)
    monkeypatch.setattr(design.run_service,'load_candidate',load)
    async def turn(_state,**kwargs):
        assert 'read_working_memory' not in {item['function']['name'] for item in kwargs['tools']}
        call={'id':'empty','name':'read_working_memory','input':{}}
        history=[*kwargs['history'],{'role':'assistant','tool_calls':[{'id':'empty','type':'function',
            'function':{'name':call['name'],'arguments':'{}'}}]}]
        return call,history,{'model_calls':19}
    monkeypatch.setattr(design,'agent_tool_turn',turn)
    result=await design.cad_session({'run_id':'run','original_request':'Keep exact values',
        'cad_history':saved_notes(),'working_memory':{},'model_calls':18,'cad_invalid_tool_attempts':2})
    assert result['phase']=='cad_recovery' and result['model_calls']==19
    assert json.loads(result['cad_history'][-1]['content'])['category']=='memory_progress'
    assert notes_just_saved(result['cad_history'])
    assert 'working_memory' not in result and snapshot['files']=={}


def test_newly_captured_history_facts_enable_older_retrieval():
    history=[]
    for i in range(13):
        history.extend([{'role':'assistant','tool_calls':[{'id':str(i),'function':{'name':'read_file'}}]},
            {'role':'tool','tool_call_id':str(i),'content':json.dumps({'ok':False,
                'error':{'fingerprint':str(i),'diagnostic':'Prior failure'}})}])
    assert older_outcomes_available({},history)
