import { beforeAll, afterAll, describe, it, expect } from 'vitest';
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

let db: PGlite;
const alice = randomUUID(),
  bob = randomUUID(),
  project = randomUUID();
let runId: string;
let revisionId: string;
beforeAll(async () => {
  db = new PGlite();
  await db.exec(`create role anon;create role authenticated;create role service_role bypassrls;create schema auth;create schema storage;
  create table auth.users(id uuid primary key);create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
  grant usage on schema public,auth,storage to anon,authenticated,service_role;grant execute on function auth.uid() to authenticated;
  create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint);`);
  await db.exec(
    readFileSync('supabase/migrations/20260831021659_initial_cad_workspace.sql', 'utf8'),
  );
  await db.exec(readFileSync('supabase/migrations/20260831181720_python_services_runtime.sql', 'utf8'));
  await db.exec(readFileSync('supabase/migrations/20260901010000_cloud_artifact_staging.sql', 'utf8'));
  await db.query('insert into auth.users(id) values($1),($2)', [alice, bob]);
  await db.query(
    "insert into public.profiles(id,email,must_change_password) values($1,'alice@example.test',false),($2,'bob@example.test',false)",
    [alice, bob],
  );
  await db.query("insert into public.projects(id,owner_id,name) values($1,$2,'Test assembly')", [
    project,
    alice,
  ]);
});
afterAll(async () => {
  await db.close();
});
describe.sequential('database invariants', () => {
  it('enables RLS on every application table and blocks direct source access', async () => {
    const rows = await db.query<{ relname: string; relrowsecurity: boolean }>(
      "select relname,relrowsecurity from pg_class join pg_namespace n on n.oid=relnamespace where n.nspname='public' and relkind='r'",
    );
    expect(rows.rows.every((r) => r.relrowsecurity)).toBe(true);
    await db.exec('set role authenticated');
    await expect(db.query('select * from public.source_snapshots')).rejects.toThrow(
      'permission denied',
    );
    await expect(db.query('select * from public.model_configs')).rejects.toThrow(
      'permission denied',
    );
    await db.exec('reset role');
  });
  it('isolates projects by current user and account status', async () => {
    await db.query("select set_config('request.jwt.claim.sub',$1,false)", [bob]);
    await db.exec('set role authenticated');
    expect((await db.query('select * from public.projects')).rows).toHaveLength(0);
    await db.exec('reset role');
    await db.query("select set_config('request.jwt.claim.sub',$1,false)", [alice]);
    await db.exec('set role authenticated');
    expect((await db.query('select * from public.projects')).rows).toHaveLength(1);
    await expect(db.query("update public.profiles set role='admin'")).rejects.toThrow(
      'permission denied',
    );
    await db.exec('reset role');
  });
  it('deduplicates submitted requests and reserves a single execution slot', async () => {
    const key = randomUUID();
    const args = [project, alice, null, 'Make a plate', '[]', key];
    const first = await db.query<{ id: string }>(
      'select public.submit_run($1,$2,$3,$4,$5,$6) id',
      args,
    );
    runId = first.rows[0].id;
    expect(
      (await db.query<{ id: string }>('select public.submit_run($1,$2,$3,$4,$5,$6) id', args))
        .rows[0].id,
    ).toBe(runId);
    expect(
      (await db.query<{ ok: boolean }>("select public.claim_run($1,'worker-a') ok", [runId]))
        .rows[0].ok,
    ).toBe(true);
    expect(
      (await db.query<{ ok: boolean }>("select public.claim_run($1,'worker-b') ok", [runId]))
        .rows[0].ok,
    ).toBe(false);
  });
  it('publishes once and rejects stale revision updates', async () => {
    revisionId = randomUUID();
    const manifest = {
      schemaVersion: 1,
      units: 'mm',
      components: [],
      instances: [],
      rootComponentId: null,
    };
    const args = [
      runId,
      revisionId,
      'First validated design',
      manifest,
      { manifest, files: {} },
      [],
      null,
    ];
    expect(
      (
        await db.query<{ id: string }>(
          'select public.publish_revision($1,$2,$3,$4,$5,$6,$7) id',
          args,
        )
      ).rows[0].id,
    ).toBe(revisionId);
    expect(
      (
        await db.query<{ id: string }>(
          'select public.publish_revision($1,$2,$3,$4,$5,$6,$7) id',
          args,
        )
      ).rows[0].id,
    ).toBe(revisionId);
    expect((await db.query('select * from public.revisions')).rows).toHaveLength(1);
    await expect(
      db.query('select public.submit_run($1,$2,$3,$4,$5,$6)', [
        project,
        alice,
        null,
        'stale request',
        '[]',
        randomUUID(),
      ]),
    ).rejects.toThrow('STALE_REVISION');
  });
  it('enforces compute reservations atomically and does not double count retries', async () => {
    expect(
      (
        await db.query<{ ok: boolean }>('select public.reserve_execution($1,$2,60,100) ok', [
          runId,
          'job-a',
        ])
      ).rows[0].ok,
    ).toBe(true);
    expect(
      (
        await db.query<{ ok: boolean }>('select public.reserve_execution($1,$2,60,100) ok', [
          runId,
          'job-a',
        ])
      ).rows[0].ok,
    ).toBe(true);
    expect(
      (
        await db.query<{ ok: boolean }>('select public.reserve_execution($1,$2,60,100) ok', [
          runId,
          'job-b',
        ])
      ).rows[0].ok,
    ).toBe(false);
  });
  it('denies privileged RPC execution to a browser account', async () => {
    await db.exec('set role authenticated');
    await expect(db.query('select public.claim_run($1,$2)', [runId, 'attacker'])).rejects.toThrow(
      'permission denied',
    );
    await db.exec('reset role');
  });
  it('resumes atomically, resets repair limits, and refuses another owner', async () => {
    await db.query("update public.runs set status='paused',model_calls=12 where id=$1", [runId]);
    await db.query(
      'update public.run_private set checkpoint=\'{"repairs":3,"task":"preserved"}\'::jsonb where run_id=$1',
      [runId],
    );
    await expect(db.query('select public.resume_run($1,$2)', [runId, bob])).rejects.toThrow(
      'RUN_NOT_PAUSED',
    );
    await db.query('select public.resume_run($1,$2)', [runId, alice]);
    const row = (
      await db.query<{
        status: string;
        model_calls: number;
        checkpoint: { repairs: number; task: string };
        lease_owner: string | null;
      }>(
        'select r.status,r.model_calls,p.checkpoint,p.lease_owner from public.runs r join public.run_private p on p.run_id=r.id where r.id=$1',
        [runId],
      )
    ).rows[0];
    expect(row).toMatchObject({
      status: 'queued',
      model_calls: 0,
      checkpoint: { repairs: 0, task: 'preserved' },
      lease_owner: null,
    });
    await expect(db.query('select public.resume_run($1,$2)', [runId, alice])).rejects.toThrow(
      'RUN_NOT_PAUSED',
    );
    await db.exec('set role authenticated');
    await expect(db.query('select public.resume_run($1,$2)', [runId, alice])).rejects.toThrow(
      'permission denied',
    );
    await db.exec('reset role');
  });
  it('isolates Python environments, fences workers and rejects stale checkpoints', async () => {
    const p = randomUUID();
    await db.query("insert into projects(id,owner_id,name) values($1,$2,'Python fixture')", [p, alice]);
    const args = [p, alice, null, 'Synthetic plate', '[]', randomUUID(), 'preview'];
    const id = (await db.query<{id:string}>('select submit_run_v2($1,$2,$3,$4,$5,$6,$7) id', args)).rows[0].id;
    expect((await db.query<{id:string}>('select submit_run_v2($1,$2,$3,$4,$5,$6,$7) id', args)).rows[0].id).toBe(id);
    await expect(db.query('select submit_run_v2($1,$2,$3,$4,$5,$6,$7)', [...args.slice(0,6), 'production'])).rejects.toThrow('ENVIRONMENT_MISMATCH');
    expect((await db.query<{ok:boolean}>("select claim_run_v2($1,'a','production') ok", [id])).rows[0].ok).toBe(false);
    expect((await db.query<{ok:boolean}>("select claim_run_v2($1,'a','preview') ok", [id])).rows[0].ok).toBe(true);
    expect((await db.query<{ok:boolean}>("select claim_run_v2($1,'b','preview') ok", [id])).rows[0].ok).toBe(false);
    expect((await db.query<{v:number}>("select checkpoint_run_v2($1,'a',0,'{}',0) v", [id])).rows[0].v).toBe(1);
    await expect(db.query("select checkpoint_run_v2($1,'a',0,'{}',0)", [id])).rejects.toThrow('CHECKPOINT_CONFLICT');
    await db.query("update run_private set lease_until=now()-interval '1 second' where run_id=$1", [id]);
    expect((await db.query<{ok:boolean}>("select claim_run_v2($1,'b','preview') ok", [id])).rows[0].ok).toBe(true);
    await expect(db.query("select checkpoint_run_v2($1,'a',1,'{}',0)", [id])).rejects.toThrow('CHECKPOINT_CONFLICT');
    const failed = {identity:{candidate:'fixture'},requirements:[{status:'failed'}]};
    await expect(db.query("select publish_revision_v2($1,'b',$2,'Rejected','{}','{}','[]',$3,null)", [id,randomUUID(),failed])).rejects.toThrow('VALIDATION_REQUIRED');
    await expect(db.query("select publish_revision_v2($1,'a',$2,'Rejected','{}','{}','[]',$3,null)", [id,randomUUID(),failed])).rejects.toThrow('LEASE_LOST');
    await db.query("update runs set status='cancelled' where id=$1", [id]);
    await db.query("select finish_run_v2($1,'b','succeeded','Must not succeed','{}')", [id]);
    expect((await db.query<{status:string}>('select status from runs where id=$1',[id])).rows[0].status).toBe('cancelled');
  });
  it('keeps trace payloads, operation ledgers and staged object paths private', async () => {
    await db.exec('set role authenticated');
    for (const table of ['trace_outbox','run_operations','artifact_staging']) {
      await expect(db.query(`select * from public.${table}`)).rejects.toThrow('permission denied');
    }
    await db.exec('reset role');
  });
});
