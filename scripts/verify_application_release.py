"""Authenticated release checks using the real build/upload/publication adapters.

The initial revision is a deterministic qualification fixture, not an AI result.
The separate chat action exercises the deployed model/workflow path from it.
All private receipts and downloaded files stay in the supplied evidence directory.
"""
import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from assembly_release_acceptance import fixture


async def main(args):
    values = dotenv_values(args.env_file)
    os.environ.update({k: v for k, v in values.items() if v and not v.startswith("[SENSITIVE]")})
    runtime = json.loads(args.runtime.read_text())
    os.environ.update(CAD_RUNTIME_SNAPSHOT_ID=runtime["snapshotId"], CAD_RUNTIME_VERSION=runtime["runtimeVersion"],
        FORMA_ENVIRONMENT=args.environment,
        VERCEL_TOKEN=json.loads(args.cli_auth_file.read_text())["token"],
        VERCEL_TEAM_ID=args.team_id, VERCEL_PROJECT_ID=args.project_id)
    os.environ.pop("VERCEL_OIDC_TOKEN", None)
    from forma_api import db, engine
    from forma_api.config import settings
    from forma_api.contracts import AppSettings, Snapshot
    from forma_api.execution import identity
    settings.cache_clear()
    folder = args.report_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "application-state.json"
    state = json.loads(state_path.read_text()) if state_path.exists() else {
        "deployment": args.base_url, "runtimeVersion": runtime["runtimeVersion"], "cases": {}}
    if state["deployment"] != args.base_url or state["runtimeVersion"] != runtime["runtimeVersion"]:
        raise ValueError("Use separate evidence directories for different deployments/runtimes")
    def save(): state_path.write_text(json.dumps(state, indent=2) + "\n")
    credentials = dict(line.split(":", 1) for line in args.credentials.read_text().splitlines() if ":" in line)
    async with httpx.AsyncClient(timeout=30) as platform:
        response = await platform.get("https://api.vercel.com/v9/projects/" + args.project_id,
            params={"teamId": args.team_id}, headers={"Authorization": "Bearer " + os.environ["VERCEL_TOKEN"]})
        if response.status_code != 200: raise RuntimeError("Could not read existing project automation protection")
        bypass = next((key for key, value in (response.json().get("protectionBypass") or {}).items()
                       if value.get("scope") == "automation-bypass"), None)
    web_headers = {"Origin": args.base_url, **({"x-vercel-protection-bypass": bypass} if bypass else {})}
    async with httpx.AsyncClient(base_url=args.base_url, timeout=120, follow_redirects=False,
                                 headers=web_headers) as web:
        async def call(method, path, **kwargs):
            for attempt in range(3 if method == "GET" else 1):
                try:
                    response = await web.request(method, path, **kwargs)
                    if response.status_code < 500 or attempt == 2: break
                except httpx.TransportError:
                    if method != "GET" or attempt == 2: raise
                await asyncio.sleep(attempt + 1)
            if not response.is_success: raise RuntimeError(f"Application {method} {path} returned HTTP {response.status_code}")
            return response.json()
        async def download(url):
            # These are read-only object requests; no charged build/model is retried.
            for attempt in range(3):
                try:
                    async with httpx.AsyncClient(timeout=120) as files:
                        response = await files.get(url)
                    if response.status_code != 200: raise RuntimeError("Private artifact download failed")
                    return response
                except httpx.TransportError:
                    if attempt == 2: raise
                    await asyncio.sleep(attempt + 1)
        await call("POST", "/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
        session = await call("GET", "/api/session")
        assert session["configured"] and session["profile"]["role"] == "admin"
        state["authentication"] = "passed"
        models = await call("GET", "/api/admin/models")
        state["modelConnections"] = [{k: row.get(k) for k in ["role", "provider", "model_id", "active", "tested_at"]} for row in models]
        if not state.get("projectId"):
            project = await call("POST", "/api/projects", json={"name": "Native assembly release qualification"})
            state["projectId"], state["ownerId"] = project["id"], project["owner_id"]
            save()
        if args.action == "pipeline":
            for thickness in [args.thickness_only] if args.thickness_only else [2, 4]:
                key = f"sixty-parts-{thickness}mm"
                if state["cases"].get(key, {}).get("status") == "passed": continue
                if key in state["cases"]: raise ValueError("Incomplete pipeline case exists; inspect its run/receipts before retrying")
                worker = "release-qualification-" + uuid4().hex
                case = state["cases"][key] = {"status": "preparing", "idempotencyKey": str(uuid4()), "worker": worker}
                save()
                run_id = await db.rpc("submit_run_v3", {"p_project": state["projectId"], "p_owner": state["ownerId"],
                    "p_base": state.get("revisionId"), "p_message": f"Deterministic native 60-part qualification at {thickness} mm; not an AI-generated result.",
                    "p_selected": [], "p_key": case["idempotencyKey"], "p_environment": args.environment})
                case["runId"] = run_id
                save()
                if not await db.rpc("claim_run_v3", {"p_run": run_id, "p_worker": worker, "p_environment": args.environment}):
                    raise RuntimeError("Qualification run could not acquire the existing worker lease")
                run = await db.one("runs", {"id": "eq." + run_id})
                snapshot = Snapshot.model_validate(fixture(thickness)).model_dump()
                cp = {"snapshot": snapshot, "requirements": [{"id": "inventory", "description": "60 physical parts", "kind": "solid_count", "count": 60}],
                    "attempts": 0, "repairs": 0, "sandbox": "forma-app-release-" + uuid4().hex,
                    "validator": "forma-app-validator-" + uuid4().hex, "startedNs": time.time_ns(), "role": "coordinator"}
                case.update(status="building", sandbox=cp["sandbox"], validator=cp["validator"])
                save()
                limits = AppSettings.model_validate((await db.one("app_settings", {"id": "eq.true"}))["settings"])
                try:
                    report = await engine.build_candidate(run, cp, limits.limits, "release:" + key)
                    assert report["identity"] == identity(snapshot, cp["requirements"])
                    assert report["bom"]["flat"][0]["quantity"] == 60
                    assert report["assemblyPlacement"]["solidChecks"] == 60
                    case["identity"], case["artifactCount"] = report["identity"], len(cp["validated"]["artifacts"])
                    save()
                    publish = {"id": "qualification-publish", "name": "publish_revision", "input": {"summary": f"Qualified deterministic 60-part fixture, {thickness} mm"}}
                    try:
                        await engine.execute_tool(run, cp, publish, limits, "wrong-worker")
                    except Exception as error:
                        if getattr(error, "status_code", None) != 409: raise
                        case["leaseFence"] = "passed"
                    else: raise AssertionError("Wrong worker published a revision")
                    result = await engine.execute_tool(run, cp, publish, limits, worker)
                    state["revisionId"] = case["revisionId"] = result["revisionId"]
                    save()
                    await db.rpc("finish_graph_run_v3", {"p_run": run_id, "p_worker": worker, "p_status": "succeeded",
                        "p_message": "Deterministic qualification fixture passed; draft for review, not engineering approval."})
                    case["runFinished"] = True
                    save()
                    workspace = await call("GET", "/api/projects/" + state["projectId"])
                    revision = next(r for r in workspace["revisions"] if r["id"] == state["revisionId"])
                    assert revision["validation"]["bom"]["flat"][0]["quantity"] == 60
                    assert revision["validation"]["nativeAssembly"]["degreesOfFreedom"] == 0
                    artifact_hashes = {}
                    for artifact in workspace["artifacts"]:
                        if artifact["revision_id"] != state["revisionId"]: continue
                        signed = await call("GET", "/api/artifacts/" + artifact["id"])
                        # Do not forward Vercel bypass credentials or application
                        # headers to Supabase's signed object URL.
                        downloaded = await download(signed["url"])
                        assert len(downloaded.content) == artifact["bytes"]
                        destination = folder / key
                        destination.mkdir(exist_ok=True)
                        (destination / Path(artifact["name"]).name).write_bytes(downloaded.content)
                        artifact_hashes[artifact["name"]] = hashlib.sha256(downloaded.content).hexdigest()
                    assert {"bom.json", "bom-flat.csv", "bom-structured.csv", "assembly.json", "assembly.step"} <= artifact_hashes.keys()
                    case.update(status="passed", authenticatedDownloads=artifact_hashes)
                    save()
                    print(key + " passed: native build, private upload, lease-fenced publication and authenticated downloads", flush=True)
                except BaseException:
                    case["status"] = "interrupted"
                    save()
                    if not case.get("runFinished"):
                        await db.rpc("finish_graph_run_v3", {"p_run": run_id, "p_worker": worker, "p_status": "paused",
                            "p_message": "Qualification interrupted; inspect saved receipts before repeating any operation."})
                    raise
                finally:
                    await engine.destroy_sandboxes(cp)
            state["pipeline"] = "passed"
        elif args.action == "chat":
            if state.get("chatRunId"): raise ValueError("Chat already submitted; inspect status instead of duplicating")
            key = state.setdefault("chatIdempotencyKey", str(uuid4()))
            save()
            message = ("Change only the reusable plate thickness from 4 mm to 3 mm. Preserve all 60 occurrences, the 59 fixed joints, grounding, reference frames and part identity. Build and independently validate, then publish the draft with regenerated STEP, preview, native assembly evidence and BOM. This is a geometric qualification fixture, no strength or manufacturing approval is requested.")
            if args.ask_thickness:
                message = ("I want to change only the reusable plate thickness, but I have not provided the new thickness yet. Ask me for the new numeric thickness in millimeters and pause this conversation. Preserve the existing revision and do not edit, build or publish before I answer. Then preserve all 60 occurrences, 59 fixed joints, grounding, reference frames and part identity when implementing my answer. This is a geometry qualification fixture; no strength or manufacturing approval is requested.")
                state["requestedPause"] = True
                save()
            result = await call("POST", f"/api/projects/{state['projectId']}/chat", json={
                "baseRevisionId": state["revisionId"], "selectedIds": [], "idempotencyKey": key,
                "message": message})
            state["chatRunId"] = result["runId"]
        elif args.action == "resume":
            if state.get("resumeAttempted") and not (args.repeat_answer and state.get("resumeSubmitted")):
                raise ValueError("Resume was already attempted; inspect the saved run before repeating")
            run = await call("GET", "/api/runs/" + state["chatRunId"])
            if run["status"] != "waiting_input": raise ValueError("The qualification run is not waiting for its missing dimension")
            workspace = await call("GET", "/api/projects/" + state["projectId"])
            assert workspace["project"]["current_revision_id"] == state["revisionId"]
            if state.get("resumeSubmitted"):
                state.setdefault("previousResumes", []).append({"submitted": True, "status": run["status"], "observedAt": run["updated_at"]})
            state["pauseObserved"], state["resumeAttempted"] = True, True
            save()
            await call("POST", f"/api/runs/{state['chatRunId']}/resume", json={"kind": "answer",
                "message": "Use 3 mm as the new reusable plate thickness. Preserve all 60 occurrences, 59 fixed joints, grounding and reference frames. Build and independently validate and publish the draft. No strength or manufacturing approval is requested."})
            state["resumeSubmitted"] = True
        elif args.action == "continue":
            run = await call("GET", "/api/runs/" + state["chatRunId"])
            if run["status"] != "paused":
                raise ValueError("Inspect the preserved run before continuing a bounded stop")
            receipt = {"pausedAt": run["updated_at"], "attempted": True}
            previous = state.setdefault("continuations", [])
            if any(item["pausedAt"] == receipt["pausedAt"] for item in previous):
                raise ValueError("Continue was already attempted for this checkpoint; inspect its receipt")
            previous.append(receipt)
            save()
            await call("POST", f"/api/runs/{state['chatRunId']}/resume", json={"kind": "continue"})
            receipt["submitted"] = True
            save()
        elif args.action == "status":
            run = await call("GET", "/api/runs/" + state["chatRunId"])
            state["chatStatus"] = run["status"]
            print(json.dumps({k: run.get(k) for k in ["id", "status", "model_calls", "updated_at", "error"]}))
            workspace = await call("GET", "/api/projects/" + state["projectId"])
            (folder / "workspace.json").write_text(json.dumps(workspace, indent=2))
            if run["status"] == "succeeded":
                revision = next(r for r in workspace["revisions"] if r["run_id"] == run["id"])
                expected = Snapshot.model_validate(fixture(3)).model_dump(mode="json")["manifest"]
                manifest = revision["manifest"]
                assert manifest["nativeAssembly"] == expected["nativeAssembly"]
                assert sorted(manifest["joints"], key=lambda j: j["id"]) == sorted(expected["joints"], key=lambda j: j["id"])
                for name, fields in [("instances", ["definitionId", "parentId", "frame", "bomExclude"]),
                                     ("references", ["componentId", "kind", "frame"])]:
                    before = {v["id"]: v for v in expected[name]}
                    after = {v["id"]: v for v in manifest[name]}
                    assert before.keys() == after.keys()
                    assert all(before[key].get(field) == after[key].get(field) for key in before for field in fields)
                plate = next(c for c in revision["manifest"]["components"] if c["id"] == "plate")
                assert plate["parameters"]["thickness"] == 3
                assert plate["partMetadata"] == expected["components"][0]["partMetadata"]
                assert revision["validation"]["bom"]["flat"][0]["quantity"] == 60
                assert revision["validation"]["assemblyPlacement"]["solidChecks"] == 60
                assert revision["validation"]["identity"]["runtime"] == runtime["runtimeVersion"]
                downloaded = {}
                for artifact in workspace["artifacts"]:
                    if artifact["revision_id"] != revision["id"]: continue
                    signed = await call("GET", "/api/artifacts/" + artifact["id"])
                    response = await download(signed["url"])
                    assert len(response.content) == artifact["bytes"]
                    destination = folder / "chat-3mm"
                    destination.mkdir(exist_ok=True)
                    (destination / Path(artifact["name"]).name).write_bytes(response.content)
                    downloaded[artifact["name"]] = hashlib.sha256(response.content).hexdigest()
                assert {"bom.json", "assembly.json", "assembly.step", "preview.glb"} <= downloaded.keys()
                state["chatArtifactHashes"] = downloaded
                state["chatRevisionId"] = revision["id"]
                state["chatReview"] = revision["validation"].get("review", {})
                state["chatWorkflow"] = "passed"
        # An unauthenticated request must never obtain any signed private URL.
        async with httpx.AsyncClient(base_url=args.base_url, timeout=30,
                headers={"x-vercel-protection-bypass": bypass} if bypass else {}) as anonymous:
            response = await anonymous.get("/api/projects/" + state["projectId"])
            assert response.status_code == 401
        state["anonymousAccessRejected"] = True
        # The documented public reviewer is an existing separate account;
        # no test invitation or outbound email is needed.
        demo = re.search(r"Email: `([^`]+)`.*Password: `([^`]+)`", (ROOT / "README.md").read_text(encoding="utf-8"))
        if not demo: raise RuntimeError("Documented second-account fixture is unavailable")
        async with httpx.AsyncClient(base_url=args.base_url, timeout=60, headers=web_headers) as other:
            response = await other.post("/api/auth/login", json={"email": demo.group(1), "password": demo.group(2)})
            if response.status_code != 200: raise RuntimeError("Second-account authentication failed")
            profile = (await other.get("/api/session")).json()["profile"]
            assert profile["id"] != state["ownerId"]
            response = await other.get("/api/projects/" + state["projectId"])
            assert response.status_code == 404
            workspace = await call("GET", "/api/projects/" + state["projectId"])
            response = await other.get("/api/artifacts/" + workspace["artifacts"][0]["id"])
            assert response.status_code == 404
        state["otherOwnerAccessRejected"] = True
        save()
        print(json.dumps({"projectId": state["projectId"], "pipeline": state.get("pipeline"), "chatWorkflow": state.get("chatWorkflow"), "chatRunId": state.get("chatRunId")}))
    await db.close_client()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["pipeline", "chat", "status", "resume", "continue"])
    parser.add_argument("--ask-thickness", action="store_true")
    parser.add_argument("--thickness-only", type=int, choices=[2, 4])
    parser.add_argument("--repeat-answer", action="store_true")
    for name in ["env-file", "runtime", "cli-auth-file", "credentials", "report-dir"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--environment", choices=["preview", "production"], required=True)
    parser.add_argument("--team-id", default="team_Nzdcjz2LikTu8cDMJVaMiX45")
    parser.add_argument("--project-id", default="prj_eHyoZK7vTY2f6bbySwO8StXYAqHs")
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        # Avoid provider payloads, signed URLs and credential-bearing exceptions.
        print("Application release verification failed: " + type(error).__name__, file=sys.stderr)
        import traceback
        for frame in traceback.extract_tb(error.__traceback__):
            print(f"  {Path(frame.filename).name}:{frame.lineno} {frame.name}", file=sys.stderr)
        raise SystemExit(1)
