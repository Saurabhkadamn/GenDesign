"""Live authenticated preview checks. CAD executes only inside Vercel Sandbox."""
import json
from pathlib import Path
import time
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "test-results/preview-access.json").read_text())
credentials = dict(line.split(":", 1) for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
REQUEST = "Design an aluminum mounting plate, 80 × 50 × 6 mm, centered at the origin. Add four 6 mm diameter through-holes at X = ±30 mm and Y = ±15 mm. Round the four outer corners with a 3 mm radius."


def main():
    with httpx.Client(base_url=config["url"], timeout=90, follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=0)) as client:
        client.get(config["shareUrl"]).raise_for_status()
        client.headers["Origin"] = config["url"]
        health = client.get("/api/health"); health.raise_for_status()
        assert health.json()["backend"] == "python"
        login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
        login.raise_for_status()
        assert all("HttpOnly" in c and "Secure" in c for c in login.headers.get_list("set-cookie") if "forma_" in c)
        session = client.get("/api/session"); session.raise_for_status()
        assert session.json()["profile"]["role"] == "admin"
        for path in ("/api/projects", "/api/admin/models", "/api/admin/settings", "/api/admin/tracing"):
            response = client.get(path); response.raise_for_status()
        connection = client.post("/api/admin/tracing/test", json={}); connection.raise_for_status()
        assert connection.json()["available"], connection.json()
        project = client.post("/api/projects", json={"name": "Cloud acceptance · mounting plate"}); project.raise_for_status()
        project_id = project.json()["id"]
        payload = {"message": REQUEST, "baseRevisionId": None, "selectedIds": [], "idempotencyKey": str(uuid4())}
        response = client.post(f"/api/projects/{project_id}/chat", json=payload); response.raise_for_status()
        run_id = response.json()["runId"]
        duplicate = client.post(f"/api/projects/{project_id}/chat", json=payload); duplicate.raise_for_status()
        assert duplicate.json()["runId"] == run_id
        evidence = {"deployment": config["url"], "projectId": project_id, "runId": run_id,
                    "pythonHealth": True, "authentication": True, "duplicateSubmission": True, "langsmith": connection.json()}
        (ROOT / "test-results/hosted-acceptance.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps({"projectId": project_id, "runId": run_id, "checks": "Python API, cookies, session, admin, LangSmith and duplicate submission passed"}), flush=True)
        previous = None
        for _ in range(100):
            current = client.get(f"/api/runs/{run_id}"); current.raise_for_status()
            state = current.json()
            if state["status"] != previous:
                print("run status:", state["status"], flush=True); previous = state["status"]
            if state["status"] not in ("queued", "running"):
                workspace = client.get(f"/api/projects/{project_id}"); workspace.raise_for_status()
                evidence.update(status=state["status"], workspace=workspace.json())
                (ROOT / "test-results/hosted-acceptance.json").write_text(json.dumps(evidence, indent=2))
                print("run finished:", state["status"], flush=True)
                return
            time.sleep(5)
        print("Acceptance run remains active; inspect its saved run ID.")


main()
