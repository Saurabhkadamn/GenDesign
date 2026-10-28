"""Public run lifecycle operations kept outside LangGraph checkpoints."""
from . import checkpoints as checkpoints  # Public import for deployment tooling.
from .. import db, repository as repo
from ..config import settings
from contextvars import ContextVar
from copy import deepcopy

lease_context = ContextVar("candidate_lease", default=None)
candidate_transaction = ContextVar('candidate_transaction',default=None)



async def save_candidate(run_id: str, snapshot: dict, candidate_hash: str) -> None:
    pending = candidate_transaction.get()
    if pending and pending['run_id']==run_id:
        pending.update(snapshot=deepcopy(snapshot),hash=candidate_hash)
        return
    lease = lease_context.get()
    if lease and lease["run_id"] == run_id:
        version = await db.rpc("commit_research_candidate", {
            "p_run":run_id,"p_worker":lease["worker"],"p_generation":lease["generation"],
            "p_expected_version":lease["candidate_version"],"p_hash":candidate_hash,"p_snapshot":snapshot})
        lease["candidate_version"] = version
        return
    run = await db.one('runs', {'id': f'eq.{run_id}'})
    if run.get('harness_version', 3) >= 4:
        raise ValueError('Research candidate changes require the current worker lease')
    await db.insert("run_candidates", {
        "run_id": run_id, "snapshot": snapshot, "candidate_hash": candidate_hash,
        "updated_at": repo.utcnow(),
    }, conflict="run_id")


async def load_candidate(run_id: str) -> dict:
    pending = candidate_transaction.get()
    if pending and pending['run_id']==run_id and pending['snapshot'] is not None:
        return deepcopy(pending['snapshot'])
    row = await db.one("run_candidates", {"run_id": f"eq.{run_id}"})
    lease = lease_context.get()
    if lease and lease["run_id"] == run_id:
        lease["candidate_version"] = row.get("candidate_version", 0)
        if pending:
            pending['expected_version'] = lease['candidate_version']
    return row["snapshot"]


async def finish(run: dict, worker: str, status: str, message: str) -> None:
    lease = lease_context.get()
    if run.get('harness_version',3)>=4 and not lease:
        raise ValueError('Research lifecycle changes require the original worker fencing token')
    await db.rpc("finish_research_run" if lease else "finish_graph_run_v3", {
        **({"p_generation":lease["generation"]} if lease else {}),
        "p_run": run["id"], "p_worker": worker, "p_status": status, "p_message": message,
    })


async def wait_for_input(run: dict, worker: str, message: str) -> None:
    await finish(run, worker, "waiting_input", message)


async def resume(run_id: str, owner_id: str, value: dict | None = None) -> None:
    run = await db.one('runs',{'id':f'eq.{run_id}','owner_id':f'eq.{owner_id}'})
    if run.get('harness_version',3)>=4:
        await db.rpc('resume_research_run',{'p_run':run_id,'p_owner':owner_id,
            'p_environment':settings().environment,'p_resume':value})
        return
    await db.rpc("resume_graph_run_v3", {
        "p_run": run_id, "p_owner": owner_id, "p_environment": settings().environment,
    })
