"""Hosted multi-turn probe: a new LangGraph run must remember project chat.

Reads the existing ignored test credential/access files. Does not print keys.
"""
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


def wait(client: httpx.Client, run_id: str) -> str:
    for _ in range(60):
        response = client.get(f"/api/runs/{run_id}")
        response.raise_for_status()
        status = response.json()["status"]
        if status not in ("queued", "running"):
            return status
        time.sleep(3)
    return "timed_out"


def submit(client: httpx.Client, project_id: str, message: str, base_revision_id=None) -> str:
    response = client.post(f"/api/projects/{project_id}/chat", json={
        "message": message, "baseRevisionId": base_revision_id,
        "selectedIds": [], "idempotencyKey": str(uuid4()),
    })
    response.raise_for_status()
    return response.json()["runId"]


def main() -> None:
    with httpx.Client(base_url=access["url"], follow_redirects=True, timeout=60) as client:
        client.get(access["shareUrl"]).raise_for_status()
        client.headers["Origin"] = access["url"]
        login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(),
            "password": credentials["Password"].strip()})
        login.raise_for_status()
        created = client.post("/api/projects", json={"name": "Project agent memory probe"})
        created.raise_for_status()
        project_id = created.json()["id"]
        first = submit(client, project_id,
            "For this project, the enclosure gasket color is sage green. Remember that decision and acknowledge it. Do not create CAD yet.")
        first_status = wait(client, first)
        if first_status != "succeeded":
            print(json.dumps({"projectId": project_id, "firstRun": first, "status": first_status}))
            raise SystemExit("First conversational turn did not complete")
        second = submit(client, project_id, "Which gasket color did I choose earlier?")
        second_status = wait(client, second)
        workspace = client.get(f"/api/projects/{project_id}")
        workspace.raise_for_status()
        messages = workspace.json()["messages"]
        reply = next((m["content"] for m in reversed(messages)
            if m["run_id"] == second and m["role"] == "assistant"), "")
        result = {"projectId": project_id, "firstRun": first, "secondRun": second,
            "firstStatus": first_status, "secondStatus": second_status,
            "rememberedColor": "sage" in reply.lower(), "reply": reply[:600]}
        print(json.dumps(result))
        if second_status != "succeeded" or not result["rememberedColor"]:
            raise SystemExit("Project memory probe failed")


if __name__ == "__main__":
    main()
