"""Hosted regression: edit a selected part from a prior built revision."""
import json
import os
from pathlib import Path
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
access = json.loads((ROOT / "test-results" / "preview-access.json").read_text())
credentials = dict(line.split(":", 1) for line in
    (ROOT / "test-results" / "forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
project_id = os.environ["FORMA_TEST_PROJECT_ID"]


def main() -> None:
    with httpx.Client(base_url=access["url"], follow_redirects=True, timeout=90) as client:
        client.get(access["shareUrl"]).raise_for_status()
        client.headers["Origin"] = access["url"]
        client.post("/api/auth/login", json={"email": credentials["Email"].strip(),
            "password": credentials["Password"].strip()}).raise_for_status()
        workspace = client.get(f"/api/projects/{project_id}")
        workspace.raise_for_status()
        prior = workspace.json()["project"]["current_revision_id"]
        revision = next(r for r in workspace.json()["revisions"] if r["id"] == prior)
        root = revision["manifest"]["rootComponentId"]
        reply = client.post(f"/api/projects/{project_id}/chat", json={
            "message": "Edit the existing block: change its X width from 20 mm to 24 mm. Keep its Y depth 10 mm, Z height 5 mm, and its center at the origin. Rebuild and export the new draft.",
            "baseRevisionId": prior, "selectedIds": [root], "idempotencyKey": str(uuid4()),
        })
        reply.raise_for_status()
        run_id = reply.json()["runId"]
        for _ in range(120):
            response = client.get(f"/api/runs/{run_id}")
            response.raise_for_status()
            status = response.json()["status"]
            if status not in ("queued", "running"):
                break
            time.sleep(3)
        workspace = client.get(f"/api/projects/{project_id}")
        workspace.raise_for_status()
        data = workspace.json()
        latest = data["project"]["current_revision_id"]
        revision = next((r for r in data["revisions"] if r["id"] == latest), {})
        checks = revision.get("validation", {}).get("requirements", [])
        artifacts = [a["name"] for a in data["artifacts"] if a["revision_id"] == latest]
        result = {"projectId": project_id, "runId": run_id, "status": status,
            "priorRevisionId": prior, "newRevisionId": latest, "selectedId": root,
            "requirements": checks, "artifacts": artifacts}
        print(json.dumps(result))
        if status != "succeeded" or latest == prior or not artifacts:
            raise SystemExit("CAD follow-up did not publish a new artifact revision")


if __name__ == "__main__":
    main()
