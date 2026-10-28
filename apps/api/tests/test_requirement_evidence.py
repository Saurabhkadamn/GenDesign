from copy import deepcopy

import pytest

from forma_api import requirement_evidence as checks
from forma_api.acceptance_plan import state_update
from forma_api.contracts import Snapshot
from forma_api.execution import identity
from forma_api.engineering_reports import CreateEngineeringReports, compose_reports


def fixture():
    snapshot = Snapshot().model_dump()
    req = {
        "id": "axial",
        "kind": "unverified",
        "description": "Axial clearance must be 0.040 to 0.080 mm",
    }
    binding = {
        "requirement": req,
        "sourceIds": ["original"],
        "check": {
            "type": "interval",
            "calculation": {
                "formula": "housing - gear",
                "outputUnit": "mm",
                "requiredMinimum": 0.040,
                "requiredMaximum": 0.080,
                "inputs": {
                    "housing": {
                        "nominal": 20.06,
                        "minimum": 20.05,
                        "maximum": 20.07,
                        "unit": "mm",
                        "sourceRef": "original",
                    },
                    "gear": {
                        "nominal": 20,
                        "minimum": 19.98,
                        "maximum": 19.99,
                        "unit": "mm",
                        "sourceRef": "original",
                        "nominalBasis": "basic",
                    },
                },
            },
        },
    }
    state = {
        "requirements": [req],
        "acceptance_plan": {"version": "plan1", "requirements": [binding]},
        "validation": {
            "identity": identity(snapshot, [req]),
            "report": {
                "requirements": [{"id": "axial", "status": "unverified"}],
                "allRequirementsVerified": False,
            },
        },
        "review": {"coverageComplete": True},
        "reviewed_candidate_hash": "old-review",
    }
    return snapshot, state


@pytest.mark.asyncio
async def test_original_axial_failure_reaches_status_and_downloadable_report(monkeypatch):
    snapshot, state = fixture()
    saved = {}

    async def record(run, kind, key, body, **kwargs):
        saved.update(kind=kind, body=body, **kwargs)
        return {"id": "receipt"}

    monkeypatch.setattr(checks, "append_record", record)
    result = await checks.evaluate_requirement(
        {},
        snapshot,
        state,
        checks.EvaluateRequirement(requirementId="axial", acceptancePlanVersion="plan1"),
    )
    values = result["requirementCheck"]["evidence"]["result"]
    assert (values["nominal"], values["minimum"], values["maximum"]) == (0.06, 0.06, 0.09)
    assert values["status"] == "failed"
    update = state_update(result, state)
    assert update["validation"]["report"]["requirements"][0]["status"] == "failed"
    assert not update["validation"]["report"]["allRequirementsVerified"]
    assert update["review"] == {} and update["reviewed_candidate_hash"] == ""
    assert state["validation"]["report"]["requirements"][0]["status"] == "unverified"
    outputs, _ = compose_reports(
        snapshot,
        {**state, **update},
        CreateEngineeringReports(
            candidateHash=identity(snapshot, state["requirements"])["candidate"],
            intervalRecordIds=["receipt"],
        ),
        {"receipt": saved},
    )
    assert "0.06,0.06,0.09,mm,0.04,0.08,failed" in outputs["tolerance-chains.csv"][1].decode()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation,error",
    [
        ("plan", "Acceptance plan changed"),
        ("candidate", "Validate the current candidate"),
        ("source", "original sources"),
        ("no_check", "remains unverified"),
    ],
)
async def test_check_cannot_use_stale_identity_or_unsourced_input(mutation, error):
    snapshot, state = fixture()
    if mutation == "plan":
        state["acceptance_plan"]["version"] = "new"
    if mutation == "candidate":
        snapshot["files"]["parts/new.py"] = "changed"
    if mutation == "source":
        state["acceptance_plan"]["requirements"][0]["check"]["calculation"]["inputs"]["gear"][
            "sourceRef"
        ] = "invented"
    if mutation == "no_check":
        state["acceptance_plan"]["requirements"][0]["check"] = None
    with pytest.raises(ValueError, match=error):
        await checks.evaluate_requirement(
            {},
            snapshot,
            state,
            checks.EvaluateRequirement(requirementId="axial", acceptancePlanVersion="plan1"),
        )


def test_changed_check_invalidates_previous_validation_even_when_description_unchanged():
    _, state = fixture()
    plan = deepcopy(state["acceptance_plan"])
    plan["version"] = "plan2"
    plan["requirements"][0]["check"]["calculation"]["requiredMaximum"] = 0.09
    update = state_update({"ok": True, "acceptancePlan": plan}, state)
    assert update["validation"] == {} and update["review"] == {}


def test_one_passing_check_cannot_hide_missing_checks():
    _, state = fixture()
    state["requirements"].append({"id": "radial", "kind": "unverified"})
    receipt = {
        "ok": True,
        "requirementIdentity": state["validation"]["identity"],
        "requirementCheck": {
            "id": "axial",
            "status": "passed",
            "evidence": {"planVersion": "plan1"},
        },
    }
    assert not state_update(receipt, state)["validation"]["report"]["allRequirementsVerified"]
    receipt["requirementCheck"]["evidence"]["planVersion"] = "old"
    with pytest.raises(ValueError, match="different acceptance plan"):
        state_update(receipt, state)
