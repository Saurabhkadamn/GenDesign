import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { PGlite } from '@electric-sql/pglite';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';

let db: PGlite;
const alice = randomUUID(),
  bob = randomUUID(),
  project = randomUUID(),
  revision = randomUUID();
const request = { sheets: [{ id: 'plate', componentId: 'plate' }] };
const submit = (
  key: string,
  owner = alice,
  base = revision,
  payload = request,
  environment = 'preview',
) =>
  db.query<{ id: string }>('select public.submit_drawing_run($1,$2,$3,$4,$5,$6) id', [
    project,
    owner,
    base,
    key,
    environment,
    payload,
  ]);

beforeAll(async () => {
  db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role bypassrls;
    create schema auth; create schema storage; create table auth.users(id uuid primary key);
    create function auth.uid() returns uuid language sql stable as $$ select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid $$;
    grant usage on schema public,auth,storage to anon,authenticated,service_role;
    grant execute on function auth.uid() to authenticated;
    create table storage.buckets(id text primary key,name text,public boolean,file_size_limit bigint,allowed_mime_types text[]);`);
  for (const file of [
    '20260831021659_initial_cad_workspace.sql',
    '20260831181720_python_services_runtime.sql',
    '20260901202447_langgraph_runtime_fence.sql',
    '20260928094614_public_demo_account.sql',
    '20261001073730_native_assembly_bom_artifacts.sql',
    '20261004180000_engineering_drawings.sql',
  ]) {
    if (file === '20261004180000_engineering_drawings.sql') {
      // Exercise the restricted bucket case. An existing NULL allowlist means
      // all MIME types are already accepted and should remain unchanged.
      await db.query(
        "update storage.buckets set allowed_mime_types=array['application/step','model/gltf-binary'] where id='cad-private'",
      );
    }
    await db.exec(readFileSync(`supabase/migrations/${file}`, 'utf8'));
  }
  await db.query('insert into auth.users(id) values($1),($2)', [alice, bob]);
  await db.query(
    "insert into profiles(id,email,must_change_password) values($1,'alice@test.invalid',false),($2,'bob@test.invalid',false)",
    [alice, bob],
  );
  await db.query("insert into projects(id,owner_id,name) values($1,$2,'Drawing test')", [
    project,
    alice,
  ]);
  const run = (
    await db.query<{ id: string }>(
      "select public.submit_run_v3($1,$2,null,'Base design','[]',$3,'preview') id",
      [project, alice, randomUUID()],
    )
  ).rows[0].id;
  await db.query(
    "insert into revisions(id,project_id,run_id,ordinal,summary,manifest) values($1,$2,$3,1,'Accepted STEP','{}')",
    [revision, project, run],
  );
  await db.query('update projects set current_revision_id=$1 where id=$2', [revision, project]);
});
afterAll(async () => {
  await db.close();
});

describe.sequential('drawing job admission and ownership', () => {
  it('rejects malformed, empty and oversized sheet lists before run admission', async () => {
    for (const payload of [
      null,
      {},
      { sheets: 'invalid' },
      { sheets: [] },
      { sheets: Array.from({ length: 51 }, () => ({ id: 'plate' })) },
    ]) {
      await expect(
        db.query('select public.submit_drawing_run($1,$2,$3,$4,$5,$6)', [
          project,
          alice,
          revision,
          randomUUID(),
          'preview',
          payload,
        ]),
      ).rejects.toThrow('INVALID_DRAWING_REQUEST');
    }
  });
  it('deduplicates exactly and rejects changes to payload, base and environment', async () => {
    const key = randomUUID();
    const first = (await submit(key)).rows[0].id;
    expect((await submit(key)).rows[0].id).toBe(first);
    await expect(
      submit(key, alice, revision, { sheets: [{ id: 'other', componentId: 'plate' }] }),
    ).rejects.toThrow('IDEMPOTENCY_CONFLICT');
    await expect(submit(key, alice, randomUUID())).rejects.toThrow('IDEMPOTENCY_CONFLICT');
    await expect(submit(key, alice, revision, request, 'production')).rejects.toThrow(
      'IDEMPOTENCY_CONFLICT',
    );
    expect(
      (
        await db.query<{ drawing_request: unknown }>(
          'select drawing_request from runs where id=$1',
          [first],
        )
      ).rows[0].drawing_request,
    ).toEqual(request);
  });
  it('cannot convert a queued chat request into a drawing request', async () => {
    const key = randomUUID();
    const run = (
      await db.query<{ id: string }>(
        "select public.submit_run_v3($1,$2,$3,'Chat request','[]',$4,'preview') id",
        [project, alice, revision, key],
      )
    ).rows[0].id;
    await expect(submit(key)).rejects.toThrow('IDEMPOTENCY_CONFLICT');
    expect(
      (
        await db.query<{ drawing_request: unknown }>(
          'select drawing_request from runs where id=$1',
          [run],
        )
      ).rows[0].drawing_request,
    ).toBeNull();
  });
  it('rejects another owner, a stale revision and an inactive account', async () => {
    await expect(submit(randomUUID(), bob)).rejects.toThrow('Project not found');
    await expect(submit(randomUUID(), alice, randomUUID())).rejects.toThrow('STALE_REVISION');
    await db.query('update profiles set active=false where id=$1', [alice]);
    await expect(submit(randomUUID())).rejects.toThrow('ACCOUNT_INACTIVE');
    await db.query('update profiles set active=true where id=$1', [alice]);
  });
  it('keeps private drawing downloads under existing artifact RLS', async () => {
    await db.query(
      "insert into artifacts(project_id,revision_id,name,kind,bytes,storage_path) values($1,$2,'drawing-plate.pdf','drawing',20,$3)",
      [project, revision, `${alice}/${project}/drawing-plate.pdf`],
    );
    await db.query("select set_config('request.jwt.claim.sub',$1,false)", [bob]);
    await db.exec('set role authenticated');
    expect((await db.query('select * from artifacts')).rows).toHaveLength(0);
    await expect(submit(randomUUID())).rejects.toThrow('permission denied');
    await db.exec('reset role');
    await db.query("select set_config('request.jwt.claim.sub',$1,false)", [alice]);
    await db.exec('set role authenticated');
    expect((await db.query('select * from artifacts')).rows).toHaveLength(1);
    await db.exec('reset role');
  });
  it('allows vector export MIME types without making the bucket public', async () => {
    const bucket = (
      await db.query<{ public: boolean; allowed_mime_types: string[] }>(
        "select public,allowed_mime_types from storage.buckets where id='cad-private'",
      )
    ).rows[0];
    expect(bucket.public).toBe(false);
    expect(bucket.allowed_mime_types).toEqual(
      expect.arrayContaining(['image/svg+xml', 'application/pdf', 'image/vnd.dxf']),
    );
  });
});
