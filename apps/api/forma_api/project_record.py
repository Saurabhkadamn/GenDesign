"""Append-only owned project knowledge, distinct from bounded model context."""

from uuid import NAMESPACE_URL, uuid5

from pydantic import Field
from typing import Literal

from . import db
from .contracts import Contract


class ReadProjectRecords(Contract):
    kind: (
        Literal["source", "requirement", "decision", "task", "experiment", "evidence", "event"]
        | None
    ) = None
    candidateHash: str | None = Field(default=None, max_length=128)
    componentId: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
    requirementId: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, pattern=r"^[a-z_]+$")
    after: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+$")
    before: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}T[0-9:.+Z-]+$")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class RecordProjectFact(Contract):
    kind: Literal["requirement", "decision", "task", "experiment"]
    key: str = Field(min_length=1, max_length=200)
    content: dict
    sourceIds: list[str] = Field(default_factory=list, max_length=100)
    supersedes: str | None = None
    componentId: str | None = None
    requirementId: str | None = None


class ReadProjectSource(Contract):
    sourceId: str
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12000, ge=1, le=24000)


async def read_source(project_id, value: ReadProjectSource):
    from .repositories.projects import identifier
    from .tracing import sanitize

    row = await db.one(
        "project_records",
        {
            "id": f"eq.{identifier(value.sourceId)}",
            "project_id": f"eq.{project_id}",
            "kind": "eq.source",
        },
    )
    content = str(row["body"].get("content", ""))
    end = min(len(content), value.offset + value.limit)
    return {
        "sourceId": row["id"],
        "content": sanitize(content[value.offset : end]),
        "offset": value.offset,
        "characters": len(content),
        "nextOffset": end if end < len(content) else None,
    }


async def read_records(project_id: str, query: ReadProjectRecords) -> dict:
    params = {
        "project_id": f"eq.{project_id}",
        "order": "created_at.asc,id.asc",
        "offset": query.offset,
        "limit": query.limit + 1,
    }
    for field, value in [
        ("kind", query.kind),
        ("candidate_hash", query.candidateHash),
        ("component_id", query.componentId),
        ("requirement_id", query.requirementId),
    ]:
        if value is not None:
            params[field] = f"eq.{value}"
    if query.status:
        params["or"] = f"(body->>status.eq.{query.status},body->content->>status.eq.{query.status})"
    bounds = [
        f"created_at.gte.{query.after}" if query.after else "",
        f"created_at.lte.{query.before}" if query.before else "",
    ]
    if any(bounds):
        params["and"] = "(" + ",".join(b for b in bounds if b) + ")"
    rows = await db.rest("project_records", params=params)
    return {
        "records": rows[: query.limit],
        "nextOffset": query.offset + query.limit if len(rows) > query.limit else None,
        "instruction": "Sources are authoritative; agent interpretations and notes are not measured evidence.",
    }


async def append_record(
    run: dict,
    kind: str,
    key: str,
    body: dict,
    *,
    candidate_hash=None,
    source_ids=None,
    supersedes=None,
    component_id=None,
    requirement_id=None,
):
    record_id = str(uuid5(NAMESPACE_URL, f"{run['project_id']}:{key}"))
    row = {
        "id": record_id,
        "project_id": run["project_id"],
        "run_id": run.get("id"),
        "kind": kind,
        "record_key": key,
        "body": body,
        "candidate_hash": candidate_hash,
        "source_ids": source_ids or [],
        "supersedes": supersedes,
        "component_id": component_id,
        "requirement_id": requirement_id,
    }
    # Writes made by a research worker use its original lease token. HTTP
    # source-document uploads and historical imports have their own ownership
    # boundary and do not impersonate a worker.
    if run.get("id"):
        from .services.receipts import fence

        owner = fence(run)
        if owner:
            return await db.rpc("append_research_record", {**owner, "p_record": row})
    # Ignore an identical replay; never mutate an earlier historical entry.
    await db.rest(
        "project_records",
        "POST",
        params={"on_conflict": "project_id,record_key"},
        body=row,
        prefer="resolution=ignore-duplicates,return=representation",
    )
    saved = await db.one(
        "project_records", {"id": f"eq.{record_id}", "project_id": f"eq.{run['project_id']}"}
    )
    if (
        saved["body"] != body
        or saved.get("supersedes") != supersedes
        or saved.get("candidate_hash") != candidate_hash
        or saved.get("source_ids", []) != (source_ids or [])
    ):
        raise ValueError("Record key already belongs to a different fact; append a new version")
    return saved


async def record_fact(run: dict, value: RecordProjectFact, candidate_hash: str):
    from .repositories.projects import identifier

    if value.kind in {"requirement", "decision"} and not value.sourceIds:
        raise ValueError(
            "Requirements and decisions need source references; record speculation as an experiment"
        )
    for source_id in [*value.sourceIds, *([value.supersedes] if value.supersedes else [])]:
        await db.one(
            "project_records",
            {"id": f"eq.{identifier(source_id)}", "project_id": f"eq.{run['project_id']}"},
        )
    return await append_record(
        run,
        value.kind,
        value.key,
        {"content": value.content, "agentInterpretation": True},
        candidate_hash=candidate_hash,
        source_ids=value.sourceIds,
        supersedes=value.supersedes,
        component_id=value.componentId,
        requirement_id=value.requirementId,
    )


async def all_user_messages(project_id: str) -> list[dict]:
    result, offset = [], 0
    while True:
        rows = await db.rest(
            "messages",
            params={
                "project_id": f"eq.{project_id}",
                "role": "eq.user",
                "order": "created_at.asc,id.asc",
                "offset": offset,
                "limit": 200,
            },
        )
        result.extend(rows)
        if len(rows) < 200:
            return result
        offset += len(rows)


async def brief_documents(project_id: str) -> list[dict]:
    result, offset = [], 0
    while True:
        rows = await db.rest(
            "project_records",
            params={
                "project_id": f"eq.{project_id}",
                "kind": "eq.source",
                "body->>type": "eq.brief_document",
                "order": "created_at.asc,id.asc",
                "offset": offset,
                "limit": 100,
            },
        )
        result.extend(rows)
        if len(rows) < 100:
            return result
        offset += len(rows)
