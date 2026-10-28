import json
from copy import deepcopy

import pytest

from forma_api.contracts import Snapshot
from forma_api.execution import digest, identity
from forma_api.engineering_reports import CreateEngineeringReports, compose_reports
from forma_api.acceptance_plan import artifact_coverage, state_update


def fixtures():
    snapshot = Snapshot().model_dump()
    source = "def calculate():\n    return {'results': {'width': {'value':20.0,'unit':'mm'}}}\n"
    snapshot["files"]["calculations/width.py"] = source
    expected = identity(snapshot, [])
    state = {
        "requirements": [],
        "validation": {"identity": expected, "report": {"requirements": []}},
    }
    request = CreateEngineeringReports(
        candidateHash=expected["candidate"],
        intervalRecordIds=["interval"],
        calculationRecordIds=["calc"],
        comparisons=[
            {
                "label": "width",
                "calculationRecordId": "calc",
                "calculationValue": "width",
                "measurementRecordId": "measurement",
                "tolerance": 0.02,
                "toleranceSourceId": "source",
            }
        ],
    )

    def row(body):
        return {"kind": "evidence", "candidate_hash": expected["candidate"], "body": body}

    records = {
        "interval": row(
            {
                "formula": "housing - gear",
                "nominal": 0.06,
                "minimum": 0.06,
                "maximum": 0.09,
                "unit": "mm",
                "status": "failed",
                "inputs": {},
                "requiredMinimum": 0.04,
                "requiredMaximum": 0.08,
            }
        ),
        "calc": row(
            {
                "ok": True,
                "reproducible": True,
                "sourcePath": "calculations/width.py",
                "sourceHash": digest(source),
                "result": {"results": {"width": {"value": 20.0, "unit": "mm"}}},
            }
        ),
        "measurement": row(
            {
                "ok": True,
                "measurement": {
                    "identity": expected,
                    "value": 20.01,
                    "unit": "mm",
                    "request": {"configurationId": "pose2"},
                },
            }
        ),
        "source": {
            "kind": "source",
            "body": {"role": "user", "content": "Allowed width difference 0.02 mm"},
        },
    }
    return snapshot, state, request, records


def test_reports_preserve_failed_interval_and_measured_values():
    snapshot, state, request, records = fixtures()
    outputs, report = compose_reports(snapshot, state, request, records)
    assert report["intervals"][0]["maximum"] == 0.09
    assert report["intervals"][0]["status"] == "failed"
    assert report["comparisons"][0]["calculated"] == 20.0
    assert report["comparisons"][0]["measured"] == 20.01
    assert report["comparisons"][0]["configurationId"] == "pose2"
    assert b"failed" in outputs["tolerance-chains.csv"][1]
    notebook = json.loads(outputs["calculations.ipynb"][1])
    assert notebook["metadata"]["forma"]["identity"] == identity(snapshot, [])
    assert "def calculate()" in "".join(notebook["cells"][1]["source"])


@pytest.mark.parametrize("failure", ["candidate", "units", "source", "geometry_identity"])
def test_reports_reject_stale_or_inconsistent_receipts(failure):
    snapshot, state, request, records = fixtures()
    if failure == "candidate":
        records["calc"]["candidate_hash"] = "old"
    elif failure == "units":
        records["measurement"]["body"]["measurement"]["unit"] = "m"
    elif failure == "source":
        snapshot["files"]["calculations/width.py"] += "\n# changed\n"
    else:
        records["measurement"]["body"]["measurement"]["identity"] = {"candidate": "old"}
    with pytest.raises(ValueError):
        compose_reports(snapshot, state, request, records)


def test_configuration_artifact_needs_reopen_receipt():
    plan = {
        "deliverables": [
            {
                "id": "pose2",
                "description": "Second pose",
                "kind": "step",
                "name": "configuration-pose2.step",
                "configurationId": "pose2",
            }
        ]
    }
    validation = {
        "artifacts": [
            {
                "name": "configuration-pose2.step",
                "kind": "step",
                "storagePath": "owned",
                "contentHash": "hash",
            }
        ]
    }
    assert artifact_coverage(plan, validation)[0]["status"] == "unverified"
    validation["report"] = {
        "configurationExports": [
            {"id": "pose2", "artifact": "configuration-pose2.step", "reopened": True}
        ]
    }
    assert artifact_coverage(plan, validation)[0]["status"] == "verified"


def test_report_state_change_preserves_step_and_requires_current_identity():
    snapshot, state, request, records = fixtures()
    state["validation"]["artifacts"] = [{"name": "part.step", "kind": "step"}]
    old = deepcopy(state)
    result = {
        "ok": True,
        "reportArtifacts": [{"name": "report.json", "kind": "report"}],
        "reportIdentity": identity(snapshot, []),
    }
    update = state_update(result, state)
    assert [a["name"] for a in update["validation"]["artifacts"]] == ["part.step", "report.json"]
    assert state == old
    result["reportIdentity"] = {"candidate": "old"}
    with pytest.raises(ValueError):
        state_update(result, state)
