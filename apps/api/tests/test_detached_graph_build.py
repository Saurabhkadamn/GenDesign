"""Guarded v4 CAD transitions preserve one candidate and one process identity."""

import pytest

from forma_api.contracts import AppSettings, Snapshot
from forma_api.execution import identity
from forma_api.graphs import design


@pytest.mark.asyncio
async def test_detached_build_waits_then_validates_same_candidate(monkeypatch):
    run_id = "00000000-0000-0000-0000-000000000001"
    snapshot = Snapshot.model_validate({
        "manifest": {"rootComponentId": "block", "components": [{
            "id": "block", "name": "Block", "kind": "solid",
            "source": "parts/block.py", "parameters": {"width": 7.25},
        }]},
        "files": {"parts/block.py": "def build(parameters, dependencies):\n    return None\n"},
    }).model_dump()
    run = {"id": run_id, "harness_version": 4, "research_mode": True}
    calls = []
    result_receipt = {"identity": identity(snapshot, []), "exitCode": 0,
                      "clean": True, "timedOut": False}
    report = {"ok": True, "identity": result_receipt["identity"],
              "artifacts": [{"name": f"piece-{i}.step", "bytes": 8}
                            for i in range(17)]}

    async def row(_state):
        return run

    async def options():
        return AppSettings()

    async def candidate(_run_id):
        return snapshot

    async def stage(_run, checkpoint, _limits):
        checkpoint["attempts"] += 1
        checkpoint["sandboxReady"] = True
        calls.append("stage")
        return {"identity": result_receipt["identity"]}

    async def operation(_run, _key, _kind, callback, **_kwargs):
        return await callback()

    async def validate(_run, checkpoint, _limits, _key, *, defer_upload):
        assert defer_upload is True
        calls.append("validate")
        return report

    async def transfer(_run, _checkpoint, _report, _limits, batch):
        calls.append(f"transfer:{len(batch)}")
        return [{**artifact, "storagePath": artifact["name"],
                 "contentHash": "a" * 64} for artifact in batch]

    async def record(_run, checkpoint, completed_report, artifacts):
        checkpoint["validated"] = {"identity": result_receipt["identity"],
                                   "report": completed_report, "artifacts": artifacts}
        calls.append("record")

    async def release(_name):
        calls.append("release")

    class CAD:
        polls = 0

        async def start_execution(self, _name, _operation, _timeout, *, request_key):
            assert len(request_key) == 64
            calls.append("start")
            return {"processId": "cad-process"}

        async def inspect_execution(self, _name, process_id, *, request_key):
            assert process_id == "cad-process" and len(request_key) == 64
            self.polls += 1
            return ({"status": "running"} if self.polls == 1 else
                    {"status": "complete", "receipt": result_receipt})

    cad = CAD()
    monkeypatch.setattr(design, "run_row", row)
    monkeypatch.setattr(design, "app_settings", options)
    monkeypatch.setattr(design.run_service, "load_candidate", candidate)
    monkeypatch.setattr(design, "stage_build_candidate", stage)
    monkeypatch.setattr(design, "operation", operation)
    monkeypatch.setattr(design, "validate_built_candidate", validate)
    monkeypatch.setattr(design, "transfer_validated_artifacts", transfer)
    monkeypatch.setattr(design, "record_validated_candidate", record)
    monkeypatch.setattr(design, "executor", lambda: cad)
    from forma_api import maintenance
    monkeypatch.setattr(maintenance, "release_sandbox", release)

    state = {"run_id": run_id, "harness_version": 4, "research_mode": True,
             "requirements": [], "attempts": 0, "build_final": True}
    staged = await design.build(state)
    assert staged["phase"] == "build_start"
    state.update(staged)
    started = await design.build_start(state)
    assert started == {"phase": "build_poll", "build_process_id": "cad-process"}
    state.update(started)
    with pytest.raises(design.OperationPending):
        await design.build_poll(state)
    completed = await design.build_poll(state)
    assert completed["phase"] == "build_validation"
    state.update(completed)
    validated = await design.build_validation(state)
    assert validated["phase"] == "build_artifacts"
    state.update(validated)
    first = await design.build_artifacts(state)
    assert first["phase"] == "build_artifacts" and first["artifact_cursor"] == 16
    state.update(first)
    second = await design.build_artifacts(state)
    assert second["phase"] == "build_finalize" and second["artifact_cursor"] == 17
    state.update(second)
    finished = await design.build_finalize(state)
    assert finished["phase"] == "validate"
    assert finished["build_result"] == report
    assert len(finished["validation"]["artifacts"]) == 17
    assert calls == ["stage", "start", "validate", "transfer:16", "transfer:1",
                     "record", "release"]
