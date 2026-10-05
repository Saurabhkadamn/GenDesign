from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from forma_api import api
from forma_api.contracts import Snapshot
from forma_api.graphs import runner
from forma_api.main import app

PROJECT,OWNER,BASE,RUN=[str(uuid4()) for _ in range(4)]
SNAPSHOT=Snapshot.model_validate({'files':{'parts/plate.py':'def build(p,d): pass'},
    'manifest':{'components':[{'id':'plate','name':'Plate','source':'parts/plate.py','kind':'solid'}],
                'rootComponentId':'plate'}}).model_dump()


@pytest.fixture
def setup(monkeypatch):
    captured=[]
    async def profile(*args,**kwargs):return {'id':OWNER}
    async def owned(project,owner):assert (project,owner)==(PROJECT,OWNER)
    async def one(table,query):
        if table=='revisions':
            assert query=={'id':f'eq.{BASE}','project_id':f'eq.{PROJECT}'};return {'id':BASE}
        assert table=='app_settings';return {'settings':{'emergencyStop':False}}
    async def snapshot(base):assert base==BASE;return deepcopy(SNAPSHOT)
    async def rpc(name,body):captured.append((name,body));return RUN
    async def dispatch(run):assert run==RUN;captured.append('dispatched')
    monkeypatch.setattr(api,'require_profile',profile);monkeypatch.setattr(api.repo,'owned_project',owned)
    monkeypatch.setattr(api.repo,'load_snapshot',snapshot);monkeypatch.setattr(api.db,'one',one)
    monkeypatch.setattr(api.db,'rpc',rpc);monkeypatch.setattr(runner,'dispatch_run',dispatch)
    monkeypatch.delenv('VERCEL',raising=False)
    return captured


def request():
    return {'baseRevisionId':BASE,'idempotencyKey':str(uuid4()),'sheets':[{'id':'sheet','componentId':'plate'}]}


def test_direct_drawing_submission_is_private_typed_and_needs_no_model_configuration(setup):
    with TestClient(app) as client:
        response=client.post(f'/api/projects/{PROJECT}/drawings',json=request(),headers={'Origin':'http://localhost:3000'})
    assert response.status_code==202,response.text
    assert response.json()=={'runId':RUN} and response.headers['cache-control']=='private, no-store'
    name,payload=setup[0]
    assert name=='submit_drawing_run' and payload['p_base']==BASE and payload['p_owner']==OWNER
    sheet=payload['p_request']['sheets'][0]
    assert sheet['autoDimensions'] and len(sheet['views'])==4
    assert setup[1]=='dispatched'


@pytest.mark.parametrize('change',['origin','component','views','nonfinite'])
def test_invalid_drawing_requests_never_admit_or_dispatch_work(setup,change):
    data=request();headers={'Origin':'http://localhost:3000'}
    if change=='origin':headers={'Origin':'https://attacker.invalid'}
    if change=='component':data['sheets'][0]['componentId']='missing'
    if change=='views':data['sheets'][0]['views']=[{'id':'v','kind':'unknown'}]
    if change=='nonfinite':data['sheets'][0]['scale']='NaN'
    with TestClient(app) as client:response=client.post(f'/api/projects/{PROJECT}/drawings',json=data,headers=headers)
    assert response.status_code == (403 if change=='origin' else 400),response.text
    assert not setup


def test_drawing_revision_must_belong_to_the_owned_project(setup,monkeypatch):
    async def no_revision(table,query):raise HTTPException(404,'Record not found')
    monkeypatch.setattr(api.db,'one',no_revision)
    with TestClient(app) as client:response=client.post(f'/api/projects/{PROJECT}/drawings',json=request(),headers={'Origin':'http://localhost:3000'})
    assert response.status_code==404 and not setup


def test_unconfigured_hosted_checkpoint_storage_does_not_queue_work(setup,monkeypatch):
    monkeypatch.setenv('VERCEL','1');monkeypatch.delenv('SUPABASE_DATABASE_URL',raising=False)
    with TestClient(app) as client:response=client.post(f'/api/projects/{PROJECT}/drawings',json=request(),headers={'Origin':'http://localhost:3000'})
    assert response.status_code==503 and not setup
