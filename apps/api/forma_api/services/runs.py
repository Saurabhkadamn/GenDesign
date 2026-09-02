"""Public run lifecycle operations kept outside LangGraph checkpoints."""
from . import checkpoints  # re-exported for deployment tooling
from .. import db, repository as repo
from ..config import settings


async def save_candidate(run_id: str, snapshot: dict, candidate_hash: str) -> None:
    await db.insert("run_candidates", {
        "run_id": run_id, "snapshot": snapshot, "candidate_hash": candidate_hash,
        "updated_at": repo.utcnow(),
    }, conflict="run_id")


async def load_candidate(run_id: str) -> dict:
    return (await db.one("run_candidates", {"run_id": f"eq.{run_id}"}))["snapshot"]


async def finish(run: dict, worker: str, status: str, message: str) -> None:
    await db.rpc("finish_graph_run_v3", {
        "p_run": run["id"], "p_worker": worker, "p_status": status, "p_message": message,
    })


async def wait_for_input(run: dict, worker: str, message: str) -> None:
    await finish(run, worker, "waiting_input", message)


async def resume(run_id: str, owner_id: str) -> None:
    await db.rpc("resume_graph_run_v3", {
        "p_run": run_id, "p_owner": owner_id, "p_environment": settings().environment,
    })
