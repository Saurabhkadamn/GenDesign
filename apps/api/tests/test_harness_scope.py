import json
from copy import deepcopy
import pytest
from forma_api.contracts import Snapshot
from forma_api.execution import identity
from forma_api.graphs import design
from forma_api.tools import model_tools

def snapshot():
 return Snapshot.model_validate({'manifest':{'components':[{'id':'plate','name':'Plate','source':'parts/plate.py','kind':'solid'}],'rootComponentId':'plate'},
 'files':{'parts/plate.py':'def build(parameters, dependencies):\n    return None'}}).model_dump()

def test_model_facing_test_and_final_build_have_distinct_scope_contracts():
 tools={t['function']['name']:t['function'] for t in model_tools('cad')}
 assert set(tools['build']['parameters']['properties'])=={'final'}
 assert tools['test_component']['parameters']['required']==['componentId']
 assert 'final' not in tools['test_component']['parameters']['properties']

@pytest.mark.asyncio
async def test_final_component_build_is_rejected_with_actionable_scope_feedback(monkeypatch):
 async def load(_): return snapshot()
 async def forbidden(*_,**__): pytest.fail('Ambiguous final scope must not silently run an isolated probe')
 async def turn(state,**_):
  call={'id':'final','name':'build','input':{'componentId':'plate','final':True}}
  history=[{'role':'assistant','tool_calls':[{'id':'final','type':'function','function':{'name':'build','arguments':json.dumps(call['input'])}}]}]
  return call,history,{'model_calls':4,'cad_pending_actions':[]}
 monkeypatch.setattr(design.run_service,'load_candidate',load)
 monkeypatch.setattr(design,'agent_tool_turn',turn)
 monkeypatch.setattr(design,'probe_component',forbidden)
 result=await design.cad_session({'run_id':'run','original_request':'Publish the plate','model_calls':3})
 feedback=json.loads(result['cad_history'][-1]['content'])
 assert feedback['category']=='build_scope' and 'without componentId' in feedback['repairGuidance']
 assert result['model_calls']==4

@pytest.mark.asyncio
async def test_component_test_keeps_the_remaining_provider_actions(monkeypatch):
 async def load(_): return snapshot()
 queue=[{'call':{'id':'next','name':'build','input':{'final':True}},'message':{'role':'assistant','content':''}}]
 async def turn(state,**_):
  call={'id':'probe','name':'test_component','input':{'componentId':'plate'}}
  return call,[],{'model_calls':5,'cad_pending_actions':deepcopy(queue)}
 async def probe(state,*_):
  assert state['cad_pending_actions']==queue
  return {'phase':'cad_session','model_calls':5,'component_probes':{'plate':{'ok':True}}}
 monkeypatch.setattr(design.run_service,'load_candidate',load)
 monkeypatch.setattr(design,'agent_tool_turn',turn)
 monkeypatch.setattr(design,'probe_component',probe)
 result=await design.cad_session({'run_id':'run','original_request':'Test then publish','model_calls':4})
 assert result['cad_pending_actions']==queue and result['model_calls']==5

@pytest.mark.asyncio
async def test_reused_probe_cycles_replan_instead_of_spending_an_unbounded_call_budget():
 source=snapshot()
 scoped=design.component_snapshot(source,'plate')
 state={'run_id':'run','original_request':'Publish','component_probes':{'plate':{'identity':identity(scoped,[]),'ok':True}},'model_calls':6}
 for i in range(4):
  # Each independent provider decision consumes its own model ordinal.
  state['model_calls']=6+i
  call={'id':f'p{i}','name':'test_component','input':{'componentId':'plate'}}
  history=[{'role':'assistant','tool_calls':[{'id':call['id'],'type':'function','function':{'name':'test_component','arguments':json.dumps(call['input'])}}]}]
  state.update(await design.probe_component(state,source,call,history,'plate'))
 assert state['phase']=='cad_recovery' and state['model_calls']==9
