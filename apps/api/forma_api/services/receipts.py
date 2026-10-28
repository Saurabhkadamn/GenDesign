"""Version-bound engineering outputs and immutable model receipts."""

from .. import db
from .runs import lease_context


def fence(run):
    lease = lease_context.get()
    if run.get("harness_version", 3) >= 4 and (not lease or lease["run_id"] != run["id"]):
        raise ValueError("Research writes require the current worker lease")
    return (
        {"p_run": run["id"], "p_worker": lease["worker"], "p_generation": lease["generation"]}
        if lease and lease["run_id"] == run["id"]
        else None
    )


async def generation(run, record):
    owner = fence(run)
    if owner:
        await db.rpc("record_research_generation", {**owner, "p_record": record})
    else:
        await db.insert("generations", record, conflict="run_id,ordinal")


async def calculation(run, record, *, source, source_path, candidate_hash, runtime):
    owner = fence(run)
    if owner:
        await db.rpc(
            "record_research_calculation",
            {
                **owner,
                "p_record": record,
                "p_source": source,
                "p_path": source_path,
                "p_candidate": candidate_hash,
                "p_runtime": runtime,
            },
        )
    else:
        await db.insert("calculations", record, conflict="id")
        await db.insert(
            "calculation_sources",
            {"calculation_id": record["id"], "source": source, "runtime_version": runtime},
            conflict="calculation_id",
        )
