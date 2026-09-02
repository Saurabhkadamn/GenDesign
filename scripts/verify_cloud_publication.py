"""A reviewed, explicitly non-model fixture through the actual hosted workflow.

Seeds only a newly created test project's private checkpoint. All tool execution,
validation, storage, publication, cleanup and tracing still run on Vercel.
Never import or execute generated CAD on this machine.
"""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/hosted-runtime.env")
sys.path.insert(0, str(ROOT / "apps/api"))
from forma_api import db
from forma_api.contracts import Snapshot
from forma_api.requirements import explicit_requirements
from verify_execution import REQUEST, SNAPSHOT


def call(name, value):
    return {"id": "fixture-" + name, "name": name, "input": value}


async def main():
    access_file = os.getenv("FORMA_ACCEPTANCE_ACCESS", "preview6-access.json")
    config = json.loads((ROOT / "test-results" / access_file).read_text())
    credentials = dict(line.split(":", 1) for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
    async with httpx.AsyncClient(base_url=config["url"], follow_redirects=True, timeout=90,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as client:
        if config.get("shareUrl"):
            (await client.get(config["shareUrl"])).raise_for_status()
        client.headers["Origin"] = config["url"]
        login = await client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
        login.raise_for_status()
        owner = login.json()["profile"]["id"]
        response = await client.post("/api/projects", json={"name": "Verified cloud fixture · no model calls"})
        response.raise_for_status()
        project = response.json()["id"]
        run = await db.rpc("submit_run_v2", {"p_project": project, "p_owner": owner, "p_base": None,
            "p_message": "Deterministic integration fixture, no model calls. " + REQUEST,
            "p_selected": [], "p_key": str(uuid4()), "p_environment": os.getenv("FORMA_ACCEPTANCE_ENV", "preview")})
        pending = [call("apply_changes", SNAPSHOT), call("build", {}),
                   call("finish", {"message": "The reviewed fixture passed independent geometry validation."})]
        checkpoint = {"snapshot": Snapshot().model_dump(), "role": "cad", "history": {"cad": [], "coordinator": [], "engineering": []},
            "pending": pending, "requirements": explicit_requirements(REQUEST), "repairs": 0, "attempts": 0,
            "protocolRepairs": 0, "sequence": 0, "modelCalls": 0, "startedNs": time.time_ns(),
            "delegation": {"call": call("delegate", {}), "remaining": [call("publish_revision", {"summary": "Independently verified mounting plate fixture"}),
                call("finish", {"message": "Cloud integration fixture passed: independently verified 80 × 50 × 6 mm plate, four Ø6 through-holes and four R3 corners. STEP and preview are available. This was a reviewed fixture, not a live model generation."})]}}
        await db.update("run_private", {"checkpoint": checkpoint}, run_id=run)
        await db.update("runs", {"status": "paused"}, id=run)
        evidence = {"kind": "reviewed-fixture-no-model", "deployment": config["url"], "projectId": project, "runId": run}
        path = ROOT / "test-results/cloud-publication.json"
        path.write_text(json.dumps(evidence, indent=2))
        response = await client.post(f"/api/runs/{run}/continue", json={})
        response.raise_for_status()
        print(json.dumps(evidence), flush=True)
        for _ in range(100):
            response = await client.get(f"/api/runs/{run}"); response.raise_for_status()
            state = response.json()
            if state["status"] not in ("running", "queued"):
                break
            await asyncio.sleep(3)
        response = await client.get(f"/api/projects/{project}"); response.raise_for_status()
        workspace = response.json()
        evidence.update(status=state["status"], workspace=workspace)
        path.write_text(json.dumps(evidence, indent=2))
        assert state["status"] == "succeeded", "Fixture did not complete; inspect saved evidence"
        assert len(workspace["revisions"]) == 1
        report = workspace["revisions"][0]["validation"]
        assert report["allRequirementsVerified"] and len(report["requirements"]) == 5
        downloads = []
        for artifact in workspace["artifacts"]:
            signed = await client.get(f"/api/artifacts/{artifact['id']}"); signed.raise_for_status()
            async with httpx.AsyncClient(timeout=30) as download_client:
                content = await download_client.get(signed.json()["url"]); content.raise_for_status()
            assert len(content.content) == artifact["bytes"]
            if artifact["kind"] == "glb":
                assert content.content[:4] == b"glTF"
            else:
                assert b"ISO-10303-21" in content.content[:100]
            downloads.append({"name": artifact["name"], "bytes": len(content.content), "kind": artifact["kind"]})
        events = await client.get(f"/api/runs/{run}/events"); events.raise_for_status()
        ids = [int(line[4:]) for line in events.text.splitlines() if line.startswith("id: ")]
        assert ids and "event: terminal" in events.text
        replay = await client.get(f"/api/runs/{run}/events", headers={"Last-Event-ID": str(ids[-1])})
        replay.raise_for_status()
        assert "event: progress" not in replay.text and "event: terminal" in replay.text
        evidence.update(downloads=downloads, sseReplay=True, modelCalls=state["model_calls"])
        path.write_text(json.dumps(evidence, indent=2))
        print(json.dumps({"status": "passed", "downloads": downloads, "requirementsVerified": 5, "sseReplay": True}), flush=True)
    await db.close_client()


if __name__ == "__main__":
    asyncio.run(main())
