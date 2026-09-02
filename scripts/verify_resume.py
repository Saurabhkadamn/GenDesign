"""Explicit acceptance retry of one known paused free-model run on a new preview."""
import json
import os
from pathlib import Path
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
config = json.loads((ROOT / "test-results" / os.getenv("FORMA_ACCEPTANCE_ACCESS", "preview7-access.json")).read_text())
if os.getenv("FORMA_ACCEPTANCE_URL"):
    config["url"] = os.environ["FORMA_ACCEPTANCE_URL"]
credentials = dict(line.split(":", 1) for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
run_id = os.getenv("FORMA_ACCEPTANCE_RUN", "006e6204-d64c-4fff-99f1-da1c97e2a8fe")

with httpx.Client(base_url=config["url"], timeout=90, follow_redirects=True, limits=httpx.Limits(max_keepalive_connections=0)) as client:
    client.get(config["shareUrl"]).raise_for_status()
    client.headers["Origin"] = config["url"]
    login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
    login.raise_for_status()
    response = client.post(f"/api/runs/{run_id}/continue", json={}); response.raise_for_status()
    print("Checkpoint resumed on the updated Python preview.", flush=True)
    for _ in range(90):
        run = client.get(f"/api/runs/{run_id}"); run.raise_for_status()
        state = run.json()
        if state["status"] not in ("running", "queued"):
            workspace = client.get(f"/api/projects/{state['project_id']}"); workspace.raise_for_status()
            evidence = {"deployment": config["url"], "run": state, "workspace": workspace.json()}
            (ROOT / "test-results/hosted-resume.json").write_text(json.dumps(evidence, indent=2))
            print("Final state:", state["status"], flush=True)
            break
        time.sleep(5)
