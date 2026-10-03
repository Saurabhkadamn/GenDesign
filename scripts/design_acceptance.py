"""Submit, inspect, explicitly continue, and download hosted design fixtures.

Each command persists IDs immediately. Status never auto-approves engineering
changes or equates a published draft with acceptance of the specification.
"""
import argparse
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["submit", "status", "resume", "followup", "download"])
    parser.add_argument("fixture", choices=["scissor-window-regulator", "stamped-mounting-bracket"])
    parser.add_argument("--message")
    parser.add_argument("--kind", default="continue", choices=["continue", "answer", "approval"])
    args = parser.parse_args()
    base = os.getenv("FORMA_ACCEPTANCE_URL", "https://forma-cad-eosin.vercel.app").rstrip("/")
    folder = ROOT / "test-results" / "designs" / args.fixture
    folder.mkdir(parents=True, exist_ok=True)
    record_path = folder / "run.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    credentials = dict(line.split(":", 1) for line in
        (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
    with httpx.Client(base_url=base, timeout=120, follow_redirects=True) as client:
        client.headers["Origin"] = base
        client.post("/api/auth/login", json={"email": credentials["Email"].strip(),
            "password": credentials["Password"].strip()}).raise_for_status()

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            response.raise_for_status()
            return response.json()

        def save():
            record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

        if args.action == "submit":
            if record.get("runId"):
                raise SystemExit("Fixture already submitted; use status, resume, or followup.")
            if not record.get("projectId"):
                project = call("POST", "/api/projects", json={"name": args.fixture + " - MiniMax M3 Vercel"})
                record = {"projectId": project["id"], "deployment": base, "idempotencyKey": str(uuid4())}
                save()
            result = call("POST", f"/api/projects/{record['projectId']}/chat", json={
                "message": (ROOT / "fixtures" / (args.fixture + ".txt")).read_text(),
                "baseRevisionId": None, "selectedIds": [], "idempotencyKey": record["idempotencyKey"]})
            record["runId"] = result["runId"]
            save()
        elif args.action == "resume":
            call("POST", f"/api/runs/{record['runId']}/resume", json={
                "kind": args.kind, "message": args.message or "Continue the saved design and report every unresolved requirement honestly."})
        elif args.action == "followup":
            if not args.message:
                raise SystemExit("A concrete follow-up message is required.")
            workspace = call("GET", f"/api/projects/{record['projectId']}")
            result = call("POST", f"/api/projects/{record['projectId']}/chat", json={
                "message": args.message, "baseRevisionId": workspace["project"]["current_revision_id"],
                "selectedIds": [], "idempotencyKey": str(uuid4())})
            record.setdefault("previousRunIds", []).append(record["runId"])
            record["runId"] = result["runId"]
            save()

        state = call("GET", f"/api/runs/{record['runId']}")
        workspace = call("GET", f"/api/projects/{record['projectId']}")
        (folder / "workspace.json").write_text(json.dumps(workspace, indent=2), encoding="utf-8")
        (folder / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        revision = next((r for r in workspace["revisions"]
            if r["id"] == workspace["project"]["current_revision_id"]), {})
        print(json.dumps({**record, "status": state["status"], "error": state.get("error"),
            "modelCalls": state.get("model_calls"), "updatedAt": state.get("updated_at"),
            "revisionId": revision.get("id"), "artifacts": [a["name"] for a in workspace["artifacts"]],
            "messages": [m["content"] for m in workspace["messages"] if m["role"] == "assistant"][-2:],
            "events": [{"stage": e.get("stage"), "message": e["message"]} for e in workspace.get("events", [])[-5:]]}, indent=2))
        if args.action == "download":
            for item in workspace["artifacts"]:
                if item["revision_id"] != revision.get("id"):
                    continue
                signed = call("GET", f"/api/artifacts/{item['id']}")
                response = httpx.get(signed["url"], timeout=120)
                response.raise_for_status()
                name = Path(item["name"]).name
                (folder / name).write_bytes(response.content)
                print("Downloaded", name, len(response.content))


if __name__ == "__main__":
    main()
