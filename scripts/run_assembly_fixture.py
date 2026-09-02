"""Run and record the six-part IoT enclosure assembly against Production."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv("FORMA_ACCEPTANCE_URL", "https://forma-cad-eosin.vercel.app").rstrip("/")
NAME = os.getenv("FORMA_FIXTURE_NAME", "iot-sensor-enclosure-assembly")
REQUEST = (ROOT / "test-results/iot-enclosure-request.txt").read_text(encoding="utf-8")
CREDENTIALS = dict(
    line.split(":", 1)
    for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines()
    if ":" in line
)


def main() -> None:
    with httpx.Client(
        base_url=URL,
        timeout=120,
        follow_redirects=True,
        limits=httpx.Limits(max_keepalive_connections=0),
    ) as client:
        client.headers["Origin"] = URL
        login = client.post(
            "/api/auth/login",
            json={"email": CREDENTIALS["Email"].strip(), "password": CREDENTIALS["Password"].strip()},
        )
        login.raise_for_status()
        project = client.post("/api/projects", json={"name": NAME})
        project.raise_for_status()
        project_id = project.json()["id"]
        submitted = client.post(
            f"/api/projects/{project_id}/chat",
            json={
                "message": REQUEST,
                "baseRevisionId": None,
                "selectedIds": [],
                "idempotencyKey": str(uuid4()),
            },
        )
        submitted.raise_for_status()
        run_id = submitted.json()["runId"]
        resumes = 0
        transitions: list[str] = []
        state: dict = {}
        for _ in range(240):
            response = client.get(f"/api/runs/{run_id}")
            response.raise_for_status()
            state = response.json()
            status = state["status"]
            if not transitions or transitions[-1] != status:
                transitions.append(status)
                print("status:", status, flush=True)
            if status in ("succeeded", "failed", "cancelled"):
                break
            if status in ("paused", "waiting_input") and resumes < 8:
                # This fixture is pre-approved for testing. Approval is valid for
                # the engineering gate; continue is used only for an interruption.
                kind = "approval" if status == "waiting_input" else "continue"
                message = (
                    "Approved for production assembly fixture testing."
                    if kind == "approval"
                    else "Continue the saved assembly run."
                )
                resumed = client.post(
                    f"/api/runs/{run_id}/resume", json={"kind": kind, "message": message}
                )
                resumed.raise_for_status()
                resumes += 1
            time.sleep(4)

        workspace = client.get(f"/api/projects/{project_id}")
        workspace.raise_for_status()
        data = workspace.json()
        revisions = data.get("revisions", [])
        revision = revisions[0] if revisions else {}
        report = revision.get("validation", {})
        manifest = revision.get("manifest") or {}
        components = manifest.get("components", [])
        instances = manifest.get("instances", [])
        artifacts = [
            {"name": a["name"], "kind": a["kind"], "bytes": a["bytes"]}
            for a in data.get("artifacts", [])
        ]
        evidence = {
            "deployment": URL,
            "name": NAME,
            "projectId": project_id,
            "runId": run_id,
            "status": state.get("status"),
            "transitions": transitions,
            "resumes": resumes,
            "revisionId": data.get("project", {}).get("current_revision_id"),
            "componentIds": [c.get("id") for c in components],
            "instanceIds": [i.get("id") for i in instances],
            "instanceDefinitionIds": [i.get("definitionId") for i in instances],
            "artifacts": artifacts,
            "validation": report,
        }
        out = ROOT / "test-results" / "production-iot-sensor-enclosure-assembly.json"
        out.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        print(
            json.dumps(
                {
                    "projectId": project_id,
                    "runId": run_id,
                    "status": state.get("status"),
                    "revisionId": evidence["revisionId"],
                    "componentIds": evidence["componentIds"],
                    "instanceCount": len(instances),
                    "artifacts": artifacts,
                }
            ),
            flush=True,
        )
        if state.get("status") != "succeeded" or not evidence["revisionId"]:
            raise SystemExit("assembly fixture did not publish a revision")
        part_components = [c for c in components if c.get("kind") != "assembly"]
        screw_instances = [i for i in instances if "screw" in str(i.get("definitionId", "")).lower()]
        if len(part_components) < 6 or len(screw_instances) != 4:
            raise SystemExit("assembly fixture did not contain six parts and four screw instances")


if __name__ == "__main__":
    main()
