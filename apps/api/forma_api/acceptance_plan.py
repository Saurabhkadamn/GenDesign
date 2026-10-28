"""Sourced acceptance interpretations and artifact coverage, never agent self-acceptance."""

from typing import Literal
from copy import deepcopy

from pydantic import Field, model_validator

from .contracts import Contract, Requirement, SafeId
from .execution import digest
from .project_record import all_user_messages, brief_documents, append_record
from . import db
from .requirement_evidence import IntervalCheck, GeometryCheck


class RequirementBinding(Contract):
    requirement: Requirement
    sourceIds: list[str] = Field(min_length=1)
    check: IntervalCheck | GeometryCheck | None = Field(default=None, discriminator="type")


class Deliverable(Contract):
    id: SafeId
    description: str = Field(min_length=1, max_length=1000)
    kind: Literal["step", "glb", "bom", "assembly", "drawing", "notebook", "report"]
    name: str = Field(min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_.-]+$")
    configurationId: SafeId | None = None
    sourceIds: list[str] = Field(min_length=1)


class PlanAmendment(Contract):
    kind: Literal["requirement", "deliverable"]
    id: SafeId
    action: Literal["revise", "retire"]
    reason: str = Field(min_length=8, max_length=2000)
    sourceIds: list[str] = Field(min_length=1)


class ReadAcceptancePlan(Contract):
    pass


class AcceptancePlan(Contract):
    baseVersion: str | None = None
    sourceIds: list[str] = Field(min_length=1)
    requirements: list[RequirementBinding] = Field(min_length=1)
    deliverables: list[Deliverable] = Field(min_length=1)
    interpretationNotes: list[str] = Field(default_factory=list)
    amendments: list[PlanAmendment] = Field(default_factory=list)

    @model_validator(mode="after")
    def distinct_ids(self):
        for ids in (
            [r.requirement.id for r in self.requirements],
            [d.id for d in self.deliverables],
        ):
            if len(ids) != len(set(ids)):
                raise ValueError(
                    "Requirement and deliverable IDs must be unique within their lists"
                )
        sources = set(self.sourceIds)
        if any(
            not set(item.sourceIds) <= sources
            for item in [*self.requirements, *self.deliverables, *self.amendments]
        ):
            raise ValueError("Every interpretation must refer to a source included in this plan")
        keys = [(a.kind, a.id) for a in self.amendments]
        if len(keys) != len(set(keys)):
            raise ValueError("Each changed item needs one unambiguous amendment")
        return self


async def current_plan(project_id):
    pointer = await db.one(
        "project_acceptance_plans", {"project_id": f"eq.{project_id}"}, required=False
    )
    if not pointer:
        return {}
    row = await db.one(
        "project_records",
        {"id": f"eq.{pointer['record_id']}", "project_id": f"eq.{project_id}", "kind": "eq.task"},
    )
    return row["body"]


def validate_amendments(previous, plan):
    if plan["baseVersion"] != previous.get("version"):
        raise ValueError(
            "Acceptance plan baseVersion is stale; retrieve the current project plan before amending it"
        )
    amendments = {(a["kind"], a["id"]): a for a in plan["amendments"]}
    changed = set()
    for kind, field in [("requirement", "requirements"), ("deliverable", "deliverables")]:
        key = (
            (lambda item: item["requirement"]["id"])
            if kind == "requirement"
            else (lambda item: item["id"])
        )
        current = {key(item): item for item in plan[field]}
        for old in previous.get(field, []):
            item_id = key(old)
            if old == current.get(item_id):
                continue
            changed.add((kind, item_id))
            amendment = amendments.get((kind, item_id))
            action = "revise" if item_id in current else "retire"
            if not amendment or amendment["action"] != action:
                raise ValueError(
                    f"{kind} {item_id} cannot be silently changed or dropped; provide a sourced {action} amendment"
                )
    if changed != set(amendments):
        raise ValueError(
            "Amendments must identify actual changes to existing requirements or deliverables"
        )


async def record_plan(run, value: AcceptancePlan, state):
    from .repositories.projects import identifier

    required_sources = {m["id"] for m in await all_user_messages(run["project_id"])}
    required_sources.update(item["id"] for item in await brief_documents(run["project_id"]))
    if not required_sources <= set(value.sourceIds):
        raise ValueError(
            "Acceptance plan omits historical user sources; retrieve and account for all corrections"
        )
    for source_id in value.sourceIds:
        source = await db.one(
            "project_records",
            {
                "id": f"eq.{identifier(source_id)}",
                "project_id": f"eq.{run['project_id']}",
                "kind": "eq.source",
            },
        )
        if source["body"].get("role") == "assistant":
            raise ValueError("Assistant claims cannot be authoritative requirement sources")
    plan = value.model_dump()
    version = digest(plan)
    previous = await current_plan(run["project_id"])
    if previous.get("version") == version:
        return {
            "ok": True,
            "acceptancePlan": previous,
        }  # crash after pointer commit, before graph checkpoint
    validate_amendments(previous, plan)
    previous_bindings = {r["requirement"]["id"]: r for r in previous.get("requirements", [])}
    record_ids = {}
    for item in plan["requirements"]:
        rid = item["requirement"]["id"]
        old = previous_bindings.get(rid)
        supersedes = previous.get("requirementRecordIds", {}).get(rid) if old != item else None
        # Identical interpretations keep their original record; changed intent appends a version.
        if old == item and previous.get("requirementRecordIds", {}).get(rid):
            record_ids[rid] = previous["requirementRecordIds"][rid]
            continue
        row = await append_record(
            run,
            "requirement",
            "acceptance:" + version + ":" + rid,
            {"content": item["requirement"], "agentInterpretation": True},
            source_ids=item["sourceIds"],
            requirement_id=rid,
            supersedes=supersedes,
        )
        record_ids[rid] = row["id"]
    plan.update(version=version, requirementRecordIds=record_ids)
    saved = await append_record(
        run, "task", "acceptance-plan:" + version, plan, source_ids=value.sourceIds
    )
    from .services.runs import lease_context

    lease = lease_context.get()
    if not lease or lease["run_id"] != run["id"]:
        raise ValueError("Acceptance plan changes require the current project worker lease")
    await db.rpc(
        "commit_acceptance_plan",
        {
            "p_run": run["id"],
            "p_worker": lease["worker"],
            "p_generation": lease["generation"],
            "p_expected": previous.get("version"),
            "p_version": version,
            "p_record": saved["id"],
        },
    )
    return {
        "ok": True,
        "acceptancePlan": plan,
        "instruction": "This is a sourced interpretation, pending independent coverage review. No requirement passed merely by recording this plan.",
    }


def state_update(result, state):
    """Only server-created result fields may update acceptance state."""
    if result.get("ok") and result.get("requirementCheck"):
        validation = deepcopy(state.get("validation") or {})
        if validation.get("identity") != result.get("requirementIdentity"):
            raise ValueError("Requirement evidence is stale")
        item = result["requirementCheck"]
        plan = state.get("acceptance_plan") or {}
        if item.get("evidence", {}).get("planVersion") != plan.get("version"):
            raise ValueError("Requirement evidence belongs to a different acceptance plan")
        expected_ids = {r["id"] for r in state.get("requirements", [])}
        if item["id"] not in expected_ids:
            raise ValueError("Requirement evidence is not part of the current requirements")
        report = validation.setdefault("report", {})
        checks = report.get("requirements", [])
        report["requirements"] = [c for c in checks if c["id"] != item["id"]] + [item]
        actual = {c["id"]: c for c in report["requirements"]}
        report["allRequirementsVerified"] = bool(expected_ids) and all(
            actual.get(rid, {}).get("status") == "passed" for rid in expected_ids
        )
        return {"validation": validation, "review": {}, "reviewed_candidate_hash": ""}
    if result.get("ok") and result.get("reportArtifacts"):
        validation = deepcopy(state.get("validation") or {})
        if validation.get("identity") != result.get("reportIdentity"):
            raise ValueError("Report receipt no longer matches the current candidate evidence")
        new = {item["name"]: item for item in result["reportArtifacts"]}
        validation["artifacts"] = [
            a for a in validation.get("artifacts", []) if a["name"] not in new
        ] + list(new.values())
        return {"validation": validation, "review": {}, "reviewed_candidate_hash": ""}
    plan = result.get("acceptancePlan") if result.get("ok") else None
    if not plan:
        return {}
    requirements = [item["requirement"] for item in plan["requirements"]]
    update = {
        "acceptance_plan": plan,
        "requirements": requirements,
        "review": {},
        "reviewed_candidate_hash": "",
        "task_outcome": "in_progress",
    }
    if requirements != state.get("requirements") or plan.get("version") != (
        state.get("acceptance_plan") or {}
    ).get("version"):
        update.update(validation={}, build_result={})
    return update


def artifact_coverage(plan, validation):
    artifacts = validation.get("artifacts") or []
    configurations = {
        item["id"]: item for item in validation.get("report", {}).get("configurationExports", [])
    }
    outcomes = []
    for requested in plan.get("deliverables", []):
        found = [
            a
            for a in artifacts
            if a["name"] == requested["name"] and a["kind"] == requested["kind"]
        ]
        complete = (
            len(found) == 1
            and bool(found[0].get("storagePath"))
            and bool(found[0].get("contentHash"))
        )
        if requested.get("configurationId"):
            receipt = configurations.get(requested["configurationId"], {})
            complete = (
                complete
                and receipt.get("reopened") is True
                and receipt.get("artifact") == requested["name"]
            )
        outcomes.append(
            {
                "id": requested["id"],
                "description": requested["description"],
                "status": "verified" if complete else "unverified",
                "name": requested["name"],
                "contentHash": found[0].get("contentHash") if len(found) == 1 else None,
            }
        )
    return outcomes
