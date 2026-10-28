import { beforeAll, afterAll, describe, it, expect } from 'vitest';
import { PGlite } from '@electric-sql/pglite';
import { readFileSync, readdirSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

let db: PGlite;
const owner = randomUUID(), project = randomUUID(), otherProject = randomUUID();
beforeAll(async () => {
  db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create schema storage; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
    grant usage on schema public,auth,storage to anon,authenticated,service_role;
    create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);`);
  for (const file of readdirSync('supabase/migrations').filter(f => f.endsWith('.sql')).sort()) {
    await db.exec(readFileSync(`supabase/migrations/${file}`, 'utf8'));
  }
  await db.query('insert into auth.users(id) values($1)', [owner]);
  await db.query("insert into public.profiles(id,email,must_change_password) values($1,'research@example.test',false)", [owner]);
  await db.query("insert into public.projects(id,owner_id,name) values($1,$3,'first'),($2,$3,'second')", [project,otherProject,owner]);
});
afterAll(async () => { await db?.close(); });

async function run(projectId: string) {
  const result = await db.query<{id:string}>(
    "select public.submit_run_v3($1,$2,null,'Test design','[]',$3,'test') id",
    [projectId,owner,randomUUID()]);
  const id = result.rows[0].id;
  await db.query('update public.runs set harness_version=4,research_mode=true where id=$1',[id]);
  return id;
}

describe.sequential('v4 ownership and truthful state', () => {
  it('saves calculations atomically, invalidates changed candidates, and rejects late writers', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'calculation ownership')",[projectId,owner]);
    const id = await run(projectId);
    const claim = () => db.query<{token:number}>("select public.claim_research_run($1,'worker','test',64) token",[id]);
    const a = (await claim()).rows[0].token;
    await db.query("select public.commit_research_candidate($1,'worker',$2,0,'candidate-a','{}')",[id,a]);
    const record = {id:randomUUID(),project_id:projectId,run_id:id,result:{value:12}};
    const save = (token:number,result=record) => db.query(
      "select public.record_research_calculation($1,'worker',$2,$3,'source','calculations/check.py','candidate-a','runtime')",
      [id,token,JSON.stringify(result)]);
    await save(a); await save(a);
    expect((await db.query('select * from public.calculation_sources where calculation_id=$1',[record.id])).rows).toHaveLength(1);
    await expect(save(a,{...record,result:{value:13}})).rejects.toThrow('IMMUTABLE_CALCULATION_RECEIPT');
    const generation = {id:randomUUID(),run_id:id,ordinal:0,role:'engineering',model_id:'pinned-model',
      config_version:1,prompt_version:'v4',status:'complete',output:{text:'receipt'},input_tokens:20,output_tokens:10};
    const saveGeneration = (token:number,value= generation) => db.query(
      "select public.record_research_generation($1,'worker',$2,$3)",[id,token,JSON.stringify(value)]);
    await saveGeneration(a); await saveGeneration(a);
    const evidence = {id:randomUUID(),project_id:projectId,run_id:id,kind:'evidence',record_key:'owned-evidence',
      body:{status:'failed'},candidate_hash:'candidate-a',source_ids:[],supersedes:null,component_id:null,requirement_id:null};
    const saveEvidence = (token:number,value=evidence) => db.query(
      "select public.append_research_record($1,'worker',$2,$3)",[id,token,JSON.stringify(value)]);
    await saveEvidence(a); await saveEvidence(a);
    await expect(saveEvidence(a,{...evidence,body:{status:'passed'}})).rejects.toThrow('IMMUTABLE_PROJECT_RECORD');
    await db.query("select public.record_research_operation($1,'worker',$2,'graph:cad-session:7','model','request','started',null)",[id,a]);
    expect((await db.query<{ordinal:number}>("select public.next_research_model_ordinal($1,'worker',$2,0) ordinal",[id,a])).rows[0].ordinal).toBe(8);
    expect((await db.query<{ordinal:number}>("select public.next_research_model_ordinal($1,'worker',$2,12) ordinal",[id,a])).rows[0].ordinal).toBe(12);
    await expect(saveGeneration(a,{...generation,output:{text:'different'}})).rejects.toThrow('IMMUTABLE_GENERATION_RECEIPT');
    const b = (await claim()).rows[0].token;
    await expect(save(a)).rejects.toThrow('LEASE_LOST');
    await expect(saveGeneration(a)).rejects.toThrow('LEASE_LOST');
    await expect(saveEvidence(a)).rejects.toThrow('LEASE_LOST');
    await db.query("select public.commit_research_candidate($1,'worker',$2,1,'candidate-b','{}')",[id,b]);
    expect((await db.query<{stale:boolean,revision_id:string|null}>('select stale,revision_id from public.calculations where id=$1',[record.id])).rows[0])
      .toEqual({stale:true,revision_id:null});
    await db.query("select public.finish_research_run($1,'worker',$2,'paused','Check complete')",[id,b]);
  });

  it('an interrupted scheduler releases its run but cannot stop a successor', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'interruption')",[projectId,owner]);
    const id = await run(projectId);
    const dispatch = async (worker:string,workflow:string) => {
      const row = await db.query<{item:{id:string}}>("select public.reserve_research_dispatch($1,$2,null) item",[id,worker]);
      await db.query("select public.prepare_research_dispatch($1,$2,$3,'{}')",[row.rows[0].item.id,worker,workflow]);
      return (await db.query<{token:number}>("select public.claim_research_run($1,$2,'test',64) token",[id,worker])).rows[0].token;
    };
    await dispatch('first','first-workflow');
    const interrupt = () => db.query<{status:string}>("select public.interrupt_research_workflow($1,'first','Execution interrupted; work saved.') status",[id]);
    expect((await interrupt()).rows[0].status).toBe('paused');
    expect((await interrupt()).rows[0].status).toBe('paused');
    expect((await db.query("select * from public.messages where run_id=$1 and role='assistant'",[id])).rows).toHaveLength(1);
    await db.query("select public.resume_research_run($1,$2,'test',null)",[id,owner]);
    const token = await dispatch('successor','second-workflow');
    expect((await interrupt()).rows[0].status).toBe('superseded');
    expect((await db.query<{status:string}>('select status from public.runs where id=$1',[id])).rows[0].status).toBe('running');
    await db.query("select public.finish_research_run($1,'successor',$2,'paused','Check complete')",[id,token]);
  });

  it('captures full brief text in an immutable source record', async () => {
    const id = await run(project);
    const text = 'x'.repeat(15000)+' REQUIRED_TAIL';
    await db.query("insert into public.messages(project_id,run_id,role,content) values($1,$2,'user',$3)",[project,id,text]);
    const records = await db.query<{body:{content:string}}>("select body from public.project_records where project_id=$1 and body->>'content'=$2",[project,text]);
    expect(records.rows[0].body.content).toBe(text);
    await db.exec('set role service_role');
    await expect(db.exec("update public.project_records set body='{}'")).rejects.toThrow('permission denied');
    await db.exec('reset role');
  });

  it('fences expired workers and permits unrelated projects', async () => {
    const first = await run(project), second = await run(otherProject);
    const claim = (id:string,worker:string) => db.query<{token:number}>(
      "select public.claim_research_run($1,$2,'test',2) token",[id,worker]);
    const a = (await claim(first,'old')).rows[0].token;
    expect(a).toBeTruthy();
    expect((await claim(second,'second')).rows[0].token).toBeTruthy();
    const competing = await run(project);
    expect((await claim(competing,'competing')).rows[0].token).toBeNull();
    await db.query("update public.run_private set lease_until=now()-interval '1 second' where run_id=$1",[first]);
    const b = (await claim(first,'new')).rows[0].token;
    expect(Number(b)).toBeGreaterThan(Number(a));
    await expect(db.query("select public.commit_research_candidate($1,'old',$2,0,'old','{}')",[first,a])).rejects.toThrow('LEASE_LOST');
    await db.query("select public.commit_research_candidate($1,'new',$2,0,'new','{}')",[first,b]);
    await expect(db.query("select public.commit_research_candidate($1,'new',$2,0,'overwrite','{}')",[first,b])).rejects.toThrow('STALE_CANDIDATE');
    const history = await db.query<{candidate_hash:string}>('select candidate_hash from public.candidate_versions where run_id=$1',[first]);
    expect(history.rows.map(r=>r.candidate_hash)).toEqual(['new']);
  });

  it('failed candidate connection leaves the active configuration intact', async () => {
    await db.exec(`insert into public.model_configs(role,model_id,encrypted_key,key_hint,active,tested_at,version)
      values('coordinator','working-model','encrypted','hint',true,now(),1);
      insert into public.model_config_candidates(role,model_id,encrypted_key,key_hint,active,version)
      values('coordinator','untested-model','encrypted','hint',false,2);`);
    const activation = await db.query<{ok:boolean}>("select public.activate_model_candidate('coordinator',2) ok");
    expect(activation.rows[0].ok).toBe(false);
    const active = await db.query<{model_id:string}>('select model_id from public.model_configs');
    expect(active.rows[0].model_id).toBe('working-model');
    await db.exec("update public.model_config_candidates set tested_at=now(),qualification='{}' where role='coordinator'");
    expect((await db.query<{ok:boolean}>("select public.activate_model_candidate('coordinator',2) ok")).rows[0].ok).toBe(false);
    await db.exec(`update public.model_config_candidates set qualification=
      '{"protocol":"multi_turn_tools_v1","identityStable":true,
        "requestedModel":"untested-model","resolvedModel":"untested-model"}'
      where role='coordinator'`);
    expect((await db.query<{ok:boolean}>("select public.activate_model_candidate('coordinator',2) ok")).rows[0].ok).toBe(true);
    expect((await db.query<{model_id:string}>('select model_id from public.model_configs')).rows[0].model_id).toBe('untested-model');
  });

  it('concurrent settings saves cannot reuse a version qualified for different inputs', async () => {
    const config = {provider:'openrouter', model_id:'candidate-one', encrypted_key:'encrypted',key_hint:'hint'};
    const stage = (value: object) => db.query<{version:number}>(
      "select public.stage_model_candidate('cad',0,$1) version",[JSON.stringify(value)]);
    expect((await stage(config)).rows[0].version).toBe(1);
    await expect(stage({...config,model_id:'candidate-two'})).rejects.toThrow('STALE_MODEL_CONFIGURATION');
    const saved = await db.query<{model_id:string}>('select model_id from public.model_config_candidates where role=$1',['cad']);
    expect(saved.rows[0].model_id).toBe('candidate-one');
  });

  it('commits candidate and node receipt together and reuses the receipt after checkpoint loss', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'replay')",[projectId,owner]);
    const id = await run(projectId);
    const token = (await db.query<{token:number}>("select public.claim_research_run($1,'worker','test',64) token",[id])).rows[0].token;
    const begin = () => db.query<{job:{status:string,result:object}}>(
      "select public.begin_research_job($1,'worker',$2,'node:edit:1','edit','input','{}') job",[id,token]);
    expect((await begin()).rows[0].job.status).toBe('running');
    await db.query("select public.commit_research_job($1,'worker',$2,'node:edit:1','input','{\"phase\":\"build\"}',0,'new-source','{\"files\":{}}')",[id,token]);
    expect((await begin()).rows[0].job).toMatchObject({status:'complete',result:{phase:'build'}});
    expect((await db.query('select * from public.candidate_versions where run_id=$1',[id])).rows).toHaveLength(1);
    await db.query("select public.begin_research_job($1,'worker',$2,'node:edit:2','edit','input2','{}')",[id,token]);
    await expect(db.query("select public.commit_research_job($1,'worker',$2,'node:edit:2','input2','{}',0,'bad','{}')",[id,token])).rejects.toThrow('STALE_CANDIDATE');
    const saved = await db.query<{status:string}>("select status from public.research_jobs where run_id=$1 and job_key='node:edit:2'",[id]);
    expect(saved.rows[0].status).toBe('running');
    expect((await db.query<{candidate_hash:string}>('select candidate_hash from public.run_candidates where run_id=$1',[id])).rows[0].candidate_hash).toBe('new-source');
  });

  it('rejects late operation receipts and allows cancellation cleanup without reporting success', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'cancel')",[projectId,owner]);
    const id = await run(projectId);
    const claim = () => db.query<{token:number}>("select public.claim_research_run($1,'worker','test',64) token",[id]);
    const a = (await claim()).rows[0].token;
    await db.query("select public.record_research_operation($1,'worker',$2,'job','build','hash','started',null)",[id,a]);
    const b = (await claim()).rows[0].token;
    await expect(db.query("select public.record_research_operation($1,'worker',$2,'job','build','hash','complete','{}')",[id,a])).rejects.toThrow('LEASE_LOST');
    await db.query("select public.record_research_operation($1,'worker',$2,'job','build','hash','complete','{\"ok\":true}')",[id,b]);
    await expect(db.query("select public.record_research_operation($1,'worker',$2,'job','build','hash','complete','{}')",[id,b])).rejects.toThrow('IMMUTABLE_OPERATION_RECEIPT');
    await db.query("update public.runs set status='cancelled' where id=$1",[id]);
    await db.query("select public.finish_research_run($1,'worker',$2,'succeeded','must not claim success')",[id,b]);
    expect((await db.query<{status:string,task_outcome:string}>('select status,task_outcome from public.runs where id=$1',[id])).rows[0]).toEqual({status:'cancelled',task_outcome:'incomplete'});
  });

  it('persists continuation intent and replays without resetting a successor', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'continuation')",[projectId,owner]);
    const id = await run(projectId);
    expect((await db.query("select * from public.execution_outbox where run_id=$1 and operation_key='dispatch:0'",[id])).rows).toHaveLength(1);
    const claim = (worker:string) => db.query<{token:number}>("select public.claim_research_run($1,$2,'test',64) token",[id,worker]);
    const token = (await claim('original')).rows[0].token;
    const rollover = (worker='original') => db.query<{id:string}>("select public.rollover_research_run($1,$2,$3,'{\"kind\":\"workflow_continuation\"}') id",[id,worker,token]);
    const outbox = (await rollover()).rows[0].id;
    expect((await rollover()).rows[0].id).toBe(outbox);
    const reserve = (worker:string) => db.query<{item:{id:string,status:string,worker_id:string}}>("select public.reserve_research_dispatch($1,$2,null) item",[id,worker]);
    expect((await reserve('creator')).rows[0].item).toMatchObject({id:outbox,status:'creating',worker_id:'creator'});
    expect((await reserve('competitor')).rows[0].item.worker_id).toBe('creator');
    await db.query("select public.prepare_research_dispatch($1,'creator','workflow-2','{}')",[outbox]);
    await db.query("select public.complete_research_dispatch($1,'workflow-2')",[outbox]);
    const next = (await claim('successor')).rows[0].token;
    expect(Number(next)).toBeGreaterThan(Number(token));
    expect((await rollover()).rows[0].id).toBe(outbox);
    const current = (await db.query<{lease_owner:string,lease_generation:number}>('select lease_owner,lease_generation from public.run_private where run_id=$1',[id])).rows[0];
    expect(current.lease_owner).toBe('successor');
    expect(current.lease_generation).toBe(next);
    await expect(rollover('intruder')).rejects.toThrow('CONTINUATION_INPUT_MISMATCH');
    await expect(db.query("select public.record_research_progress($1,'original',$2,500)",[id,token])).rejects.toThrow('LEASE_LOST');
    await db.query("update public.runs set status='paused' where id=$1",[id]);
    const response = {kind:'answer',message:'Keep the original bore diameter.'};
    await db.query("select public.resume_research_run($1,$2,'test',$3)",[id,owner,JSON.stringify(response)]);
    const resumed = (await reserve('resumed')).rows[0].item;
    expect(resumed.id).not.toBe(outbox);
    expect((await db.query<{payload:object}>('select payload from public.execution_outbox where id=$1',[resumed.id])).rows[0].payload).toEqual({resume:response});
    await expect(db.query("select public.reserve_research_dispatch($1,'other','{\"kind\":\"continue\"}')",[id])).rejects.toThrow('DISPATCH_INPUT_MISMATCH');
  });

  it('never re-creates an uncertain dispatch and preserves cancellation', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'ambiguous dispatch')",[projectId,owner]);
    const id = await run(projectId);
    const reserve = () => db.query<{item:{id:string,status:string}}>("select public.reserve_research_dispatch($1,'creator',null) item",[id]);
    const item = (await reserve()).rows[0].item;
    await db.query("update public.execution_outbox set updated_at=now()-interval '11 minutes' where id=$1",[item.id]);
    await db.query("select public.expire_research_dispatches('test')");
    expect((await reserve()).rows[0].item.status).toBe('ambiguous');
    expect((await db.query<{task_outcome:string}>('select task_outcome from public.runs where id=$1',[id])).rows[0].task_outcome).toBe('blocked');
    await db.query("update public.runs set status='cancelled',task_outcome='incomplete' where id=$1",[id]);
    const prepared = await db.query<{ok:boolean}>("select public.prepare_research_dispatch($1,'creator','late-workflow','{}') ok",[item.id]);
    expect(prepared.rows[0].ok).toBe(false);
    expect((await db.query<{ok:boolean}>("select public.complete_research_dispatch($1,'late-workflow') ok",[item.id])).rows[0].ok).toBe(false);
    expect((await db.query<{status:string}>('select status from public.runs where id=$1',[id])).rows[0].status).toBe('cancelled');
  });

  it('activates a staged provider only with a stable pinned model identity', async () => {
    await db.query(`insert into public.model_config_candidates
      (role, provider, model_id, encrypted_key, key_hint, version, tested_at, qualification)
      values ('engineering', 'openrouter', 'vendor/pinned', 'encrypted-test-value', 'test', 1, now(),
        '{"protocol":"multi_turn_tools_v1","identityStable":true,
          "requestedModel":"vendor/pinned","resolvedModel":"vendor/substitute"}')`);
    const activate = () => db.query<{ok:boolean}>(
      "select public.activate_model_candidate('engineering',1) ok");
    expect((await activate()).rows[0].ok).toBe(false);
    await db.query(`update public.model_config_candidates
      set qualification=jsonb_set(qualification,'{resolvedModel}','"vendor/pinned"')
      where role='engineering'`);
    expect((await activate()).rows[0].ok).toBe(true);
    expect((await db.query<{model_id:string}>('select model_id from public.model_configs where role=\'engineering\''))
      .rows[0].model_id).toBe('vendor/pinned');
  });

  it('rejects late progress, staged files, and resource reservations from an expired worker', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'fenced effects')",
      [projectId,owner]);
    const id = await run(projectId);
    const claim = () => db.query<{token:number}>(
      "select public.claim_research_run($1,'worker','test',64) token",[id]);
    const token = (await claim()).rows[0].token;
    const event = (generation:number) => db.query(
      "select public.append_research_event($1,'worker',$2,'status','Built draft','cad',1,null)",
      [id,generation]);
    const artifact = (generation:number) => db.query(
      "select public.stage_research_artifact($1,'worker',$2,$3,'test/file.step',42)",
      [id,generation,projectId]);
    const reserve = (generation:number) => db.query(
      "select public.reserve_research_execution($1,'worker',$2,$3,2,100)",
      [id,generation,`sandbox:${id}`]);
    await event(token); await artifact(token); await reserve(token);
    const successor = (await claim()).rows[0].token;
    expect(Number(successor)).toBeGreaterThan(Number(token));
    await expect(event(token)).rejects.toThrow('LEASE_LOST');
    await expect(artifact(token)).rejects.toThrow('LEASE_LOST');
    await expect(reserve(token)).rejects.toThrow('LEASE_LOST');
    expect((await db.query('select * from public.run_events where run_id=$1',[id])).rows).toHaveLength(1);
    expect((await db.query('select * from public.artifact_staging where run_id=$1',[id])).rows)
      .toHaveLength(1);
  });

  it('cancels a research run atomically and invalidates its scheduler and worker', async () => {
    const projectId = randomUUID();
    await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'cancelled work')",
      [projectId,owner]);
    const id = await run(projectId);
    const dispatch = (await db.query<{item:{id:string}}>(
      "select public.reserve_research_dispatch($1,'worker',null) item",[id])).rows[0].item;
    await db.query("select public.prepare_research_dispatch($1,'worker','workflow-id','{}')",
      [dispatch.id]);
    const token = (await db.query<{token:number}>(
      "select public.claim_research_run($1,'worker','test',64) token",[id])).rows[0].token;
    const cancel = () => db.query<{changed:boolean}>(
      'select public.cancel_research_run($1,$2) changed',[id,owner]);
    expect((await cancel()).rows[0].changed).toBe(true);
    expect((await cancel()).rows[0].changed).toBe(false);
    expect((await db.query<{status:string,task_outcome:string}>(
      'select status,task_outcome from public.runs where id=$1',[id])).rows[0])
      .toEqual({status:'cancelled',task_outcome:'incomplete'});
    expect((await db.query<{status:string}>(
      'select status from public.execution_outbox where id=$1',[dispatch.id])).rows[0].status)
      .toBe('cancelled');
    await expect(db.query(
      "select public.append_research_event($1,'worker',$2,'status','late','cad',1,null)",
      [id,token])).rejects.toThrow('RUN_NOT_ACTIVE');
    expect((await db.query("select * from public.messages where run_id=$1 and role='assistant'",[id])).rows)
      .toHaveLength(1);
  });
});
