"""Run one authenticated Forma fixture against the deployed production API."""
import json
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
URL = os.getenv("FORMA_ACCEPTANCE_URL", "https://forma-cad-eosin.vercel.app").rstrip("/")
NAME = os.getenv("FORMA_FIXTURE_NAME", "production-fixture")
REQUEST = os.environ["FORMA_FIXTURE_REQUEST"]
credentials = dict(
    line.split(":", 1)
    for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines()
    if ":" in line
)


def main() -> None:
    with httpx.Client(base_url=URL, timeout=120, follow_redirects=True,
                      limits=httpx.Limits(max_keepalive_connections=0)) as client:
        client.headers["Origin"] = URL
        login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(),
                                                      "password": credentials["Password"].strip()})
        login.raise_for_status()
        project = client.post("/api/projects", json={"name": NAME})
        project.raise_for_status()
        project_id = project.json()["id"]
        payload = {"message": REQUEST, "baseRevisionId": None, "selectedIds": [],
                   "idempotencyKey": str(uuid4())}
        submitted = client.post(f"/api/projects/{project_id}/chat", json=payload)
        submitted.raise_for_status()
        run_id = submitted.json()["runId"]
        resumes = 0
        transitions = []
        for _ in range(180):
            run = client.get(f"/api/runs/{run_id}")
            run.raise_for_status()
            state = run.json()
            status = state["status"]
            if not transitions or transitions[-1] != status:
                transitions.append(status)
                print("status:", status, flush=True)
            if status in ("succeeded", "failed", "cancelled"):
                break
            if status in ("paused", "waiting_input") and resumes < 4:
                # This fixture runner is intentionally explicit: simple/medium/hard
                # acceptance requests are approved for the test run, never silently
                # treated as successful when the graph has stopped.
                kind = "approval" if status == "waiting_input" else "continue"
                response = client.post(f"/api/runs/{run_id}/resume", json={
                    "kind": kind,
                    "message": "Approved for production fixture testing." if kind == "approval" else "Continue.",
                })
                response.raise_for_status()
                resumes += 1
            time.sleep(4)
        workspace = client.get(f"/api/projects/{project_id}")
        workspace.raise_for_status()
        data = workspace.json()
        revisions = data.get("revisions", [])
        report = revisions[0].get("validation", {}) if revisions else {}
        artifacts = [{"name": a["name"], "kind": a["kind"], "bytes": a["bytes"]} for a in data.get("artifacts", [])]
        evidence = {"deployment": URL, "name": NAME, "projectId": project_id, "runId": run_id,
                    "status": state["status"], "transitions": transitions, "resumes": resumes,
                    "revisionId": data.get("project", {}).get("current_revision_id"),
                    "artifacts": artifacts, "validation": report}
        out = ROOT / "test-results" / f"production-{NAME}.json"
        out.write_text(json.dumps(evidence, indent=2))
        print(json.dumps({"projectId": project_id, "runId": run_id, "status": state["status"],
                          "revisionId": evidence["revisionId"], "artifacts": artifacts,
                          "allRequirementsVerified": report.get("allRequirementsVerified", False)}), flush=True)
        if state["status"] != "succeeded" or not evidence["revisionId"] or not artifacts:
            raise SystemExit("fixture did not publish a built draft with downloadable artifacts")


if __name__ == "__main__":
    main()
