from copy import deepcopy
from uuid import uuid4

import pytest

from forma_api import acceptance_plan as plans
from forma_api.services.runs import lease_context


def definition(source):
    return {
        "sourceIds": [source],
        "requirements": [
            {
                "requirement": {
                    "id": "clearance",
                    "description": "Keep the specified clearance",
                    "kind": "unverified",
                },
                "sourceIds": [source],
            }
        ],
        "deliverables": [
            {
                "id": "state_zero",
                "description": "Reopened zero degree STEP",
                "kind": "step",
                "name": "state-zero.step",
                "configurationId": "zero",
                "sourceIds": [source],
            },
            {
                "id": "state_five",
                "description": "Reopened five degree STEP",
                "kind": "step",
                "name": "state-five.step",
                "configurationId": "five",
                "sourceIds": [source],
            },
        ],
    }


@pytest.fixture
def record_store(monkeypatch):
    source, project = str(uuid4()), str(uuid4())
    stored, pointer = {}, {}

    async def messages(_):
        return [{"id": source}]

    async def documents(_):
        return []

    async def one(table, params, **kwargs):
        if table == "project_acceptance_plans":
            return deepcopy(pointer) or None
        key = params["id"][3:]
        if key == source:
            return {"body": {"role": "user", "content": "Exact original specification"}}
        return deepcopy(stored[key])

    async def append(run, kind, key, body, **kwargs):
        for row in stored.values():
            if row["key"] == key:
                assert row["body"] == body
                return deepcopy(row)
        record = {"id": str(uuid4()), "key": key, "kind": kind, "body": deepcopy(body), **kwargs}
        stored[record["id"]] = record
        return deepcopy(record)

    async def rpc(name, args):
        assert name == "commit_acceptance_plan"
        assert args["p_expected"] == pointer.get("version")
        pointer.update(record_id=args["p_record"], version=args["p_version"])

    monkeypatch.setattr(plans, "all_user_messages", messages)
    monkeypatch.setattr(plans, "brief_documents", documents)
    monkeypatch.setattr(plans.db, "one", one)
    monkeypatch.setattr(plans.db, "rpc", rpc)
    monkeypatch.setattr(plans, "append_record", append)
    token = lease_context.set({"run_id": "first", "worker": "worker", "generation": 1})
    yield source, project, stored, pointer
    lease_context.reset(token)


@pytest.mark.asyncio
async def test_old_requirements_survive_new_run_and_missing_step_cannot_silently_disappear(
    record_store,
):
    source, project, stored, pointer = record_store
    value = definition(source)
    run = {"id": "first", "project_id": project}
    first = (await plans.record_plan(run, plans.AcceptancePlan.model_validate(value), {}))[
        "acceptancePlan"
    ]
    before = deepcopy(stored)
    # There is no old graph state in a new run. The project head is authoritative.
    changed = {**value, "baseVersion": first["version"], "deliverables": value["deliverables"][:1]}
    with pytest.raises(ValueError, match="silently changed or dropped"):
        await plans.record_plan(run, plans.AcceptancePlan.model_validate(changed), {})
    assert stored == before
    assert (await plans.current_plan(project))["deliverables"] == first["deliverables"]
    replay = await plans.record_plan(run, plans.AcceptancePlan.model_validate(value), {})
    assert replay["acceptancePlan"] == first
    assert stored == before


@pytest.mark.asyncio
async def test_explicit_sourced_amendment_appends_history_without_overwriting(record_store):
    source, project, stored, pointer = record_store
    run = {"id": "first", "project_id": project}
    value = definition(source)
    first = (await plans.record_plan(run, plans.AcceptancePlan.model_validate(value), {}))[
        "acceptancePlan"
    ]
    old_record = first["requirementRecordIds"]["clearance"]
    changed = deepcopy(value)
    changed.update(
        baseVersion=first["version"],
        amendments=[
            {
                "kind": "requirement",
                "id": "clearance",
                "action": "revise",
                "reason": "Clarify the interpretation from the original source wording.",
                "sourceIds": [source],
            }
        ],
    )
    changed["requirements"][0]["requirement"]["description"] = (
        "Keep the exact original clearance interval, including its failure"
    )
    second = (await plans.record_plan(run, plans.AcceptancePlan.model_validate(changed), {}))[
        "acceptancePlan"
    ]
    assert second["version"] != first["version"]
    assert stored[second["requirementRecordIds"]["clearance"]]["supersedes"] == old_record
    assert stored[old_record]["body"]["content"]["description"] == "Keep the specified clearance"
    with pytest.raises(ValueError, match="baseVersion is stale"):
        await plans.record_plan(run, plans.AcceptancePlan.model_validate(value), {})
