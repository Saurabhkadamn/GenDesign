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
ACCESS_FILE = os.getenv("FORMA_ACCESS_FILE")
credentials = dict(
    line.split(":", 1)
    for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines()
    if ":" in line
)


def request(client: httpx.Client, method: str, path: str, **kwargs) -> httpx.Response:
    """Keep an acceptance run observable across transient local/TLS resets."""
    last_error = None
    for attempt in range(5):
        try:
            return client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            last_error = exc
            if attempt == 4:
                raise
            time.sleep(2 * (attempt + 1))
    raise last_error  # pragma: no cover


def main() -> None:
    with httpx.Client(base_url=URL, timeout=120, follow_redirects=True,
                      limits=httpx.Limits(max_keepalive_connections=0)) as client:
        if ACCESS_FILE:
            access = json.loads(Path(ACCESS_FILE).read_text())
            shared = request(client, "GET", access["shareUrl"])
            shared.raise_for_status()
        client.headers["Origin"] = URL
        login = request(client, "POST", "/api/auth/login", json={"email": credentials["Email"].strip(),
                                                                  "password": credentials["Password"].strip()})
        login.raise_for_status()
        project = request(client, "POST", "/api/projects", json={"name": NAME})
        project.raise_for_status()
        project_id = project.json()["id"]
        payload = {"message": REQUEST, "baseRevisionId": None, "selectedIds": [],
                   "idempotencyKey": str(uuid4())}
        submitted = request(client, "POST", f"/api/projects/{project_id}/chat", json=payload)
        submitted.raise_for_status()
        run_id = submitted.json()["runId"]
        resumes = 0
        transitions = []
        for _ in range(180):
            run = request(client, "GET", f"/api/runs/{run_id}")
            run.raise_for_status()
            state = run.json()
            status = state["status"]
            if not transitions or transitions[-1] != status:
                transitions.append(status)
                print("status:", status, flush=True)
            if status in ("succeeded", "failed", "cancelled"):
                break
            if status in ("paused", "waiting_input") and resumes < 8:
                # This fixture runner is intentionally explicit: simple/medium/hard
                # acceptance requests are approved for the test run, never silently
                # treated as successful when the graph has stopped.
                kind = "answer" if status == "waiting_input" else "continue"
                response = request(client, "POST", f"/api/runs/{run_id}/resume", json={
                    "kind": kind,
                    "message": ("Use reasonable standard engineering assumptions, choose suitable materials, "
                                "and continue the requested design." if kind == "answer" else "Continue."),
                })
                response.raise_for_status()
                resumes += 1
            time.sleep(4)
        workspace = request(client, "GET", f"/api/projects/{project_id}")
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
