"""Real hosted drawing jobs and private downloads; persist submission before polling.

Uses an existing owned 60-occurrence qualification project, never customer data.
Browser authentication state is private input. No credentials or signed URLs are logged.
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx


def main(args):
    host = urlparse(args.url).hostname
    if not host or not (host.endswith("-negens-projects.vercel.app") or host == "forma-cad-eosin.vercel.app"):
        raise ValueError("Use a known Forma deployment")
    folder = args.report_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "drawing-acceptance-state.json"
    state = json.loads(path.read_text()) if path.exists() else {"projectId": args.project, "cases": {}}
    if state["projectId"] != args.project:
        raise ValueError("Evidence belongs to another project")
    def save():
        path.write_text(json.dumps(state, indent=2) + "\n")
    cookies = json.loads(args.browser_state.read_text())["cookies"]
    jar = httpx.Cookies()
    for cookie in cookies:
        if cookie["domain"].lstrip(".") != host:
            raise ValueError("Browser state belongs to another deployment")
        jar.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
    with httpx.Client(base_url=args.url, cookies=jar, headers={"Origin": args.url}, timeout=120) as web:
        def call(method, endpoint, **kwargs):
            r = web.request(method, endpoint, **kwargs)
            if not r.is_success or "application/json" not in r.headers.get("content-type", ""):
                raise RuntimeError(f"{method} {endpoint} returned {r.status_code} without expected JSON")
            return r.json()
        workspace = call("GET", "/api/projects/" + args.project)
        current = workspace["project"]["current_revision_id"]
        revision = next(r for r in workspace["revisions"] if r["id"] == current)
        if args.action in {"positive", "negative"}:
            key = args.action
            case = state["cases"].get(key)
            if not case:
                assert len(revision["manifest"]["instances"]) == 60
                if key == "positive":
                    top = {"kind": "plane", "origin": [0, 0, 1.5], "direction": [0, 0, 1]}
                    hole = {"kind": "cylinder", "origin": [0, 0, 0], "direction": [0, 0, 1]}
                    sheets = [
                        {"id": "plate_detail", "componentId": "plate", "title": "Hosted plate drawing qualification",
                         "paper": "A3", "projection": "third_angle", "standard": "ISO",
                         "dimensions": [{"id": "width_tolerance", "viewId": "top", "kind": "extent", "axis": "X",
                                         "upperTolerance": 0.1, "lowerTolerance": 0.1, "offsetMm": 18}],
                         "datums": [{"label": "A", "viewId": "top", "reference": top}],
                         "controls": [{"id": "hole_position", "viewId": "top", "reference": hole,
                                       "characteristic": "position", "toleranceMm": 0.1, "zone": "diameter",
                                       "materialCondition": "MMC", "datums": ["A"], "offset": [20, -20]}],
                         "notes": ["Qualification fixture only. GD&T is engineer-authored intent; no manufacturing approval."]},
                        {"id": "plate_section", "componentId": "plate", "title": "Native section through bore",
                         "paper": "A4", "projection": "first_angle", "standard": "ISO",
                         "views": [{"id": "section", "kind": "section", "sectionAxis": "Y", "sectionOffsetMm": 0}],
                         "autoDimensions": False},
                        {"id": "assembly_bom", "componentId": "assembly", "title": "60-occurrence assembly and BOM",
                         "paper": "A3", "views": [{"id": "isometric", "kind": "isometric"}],
                         "autoDimensions": False, "includeBom": True},
                    ]
                else:
                    assert state["cases"]["positive"]["status"] == "passed"
                    sheets = json.loads(json.dumps(revision["manifest"]["drawings"]))
                    sheets[0]["dimensions"].append({"id": "missing_bore", "viewId": "top", "kind": "diameter",
                        "reference": {"kind": "cylinder", "origin": [999, 999, 0], "direction": [0, 0, 1]}})
                case = state["cases"][key] = {"url": args.url, "status": "prepared", "baseRevision": current,
                    "request": {"baseRevisionId": current, "idempotencyKey": str(uuid4()), "sheets": sheets}}
                save()
            if case.get("runId"):
                print("Existing drawing run retained: " + case["runId"])
                return
            if case["status"] != "prepared":
                raise ValueError("Submission outcome uncertain; inspect the saved idempotency key before any replay")
            case["status"] = "submitting"
            save()
            result = call("POST", "/api/projects/" + args.project + "/drawings", json=case["request"])
            case.update(runId=result["runId"], status="submitted")
            save()
            print("Hosted drawing submitted: " + result["runId"])
            return
        if args.action == "observe":
            case = state["cases"][args.case]
            run = call("GET", "/api/runs/" + case["runId"])
            case["runStatus"] = run["status"]
            case["modelCalls"] = run["model_calls"]
            save()
            if run["status"] not in {"succeeded", "failed", "paused", "cancelled"}:
                print(json.dumps({"runId": run["id"], "status": run["status"], "modelCalls": run["model_calls"]}))
                return
            if args.case == "negative":
                assert run["status"] == "failed", "Expected missing reference rejection"
                assert current == case["baseRevision"], "Failed drawing changed the accepted revision"
                assert run["model_calls"] == 0
                assert not any(a.get("revision_id") in {r["id"] for r in workspace["revisions"] if r.get("run_id") == run["id"]}
                               for a in workspace["artifacts"])
                case.update(status="passed", lastGoodRevisionPreserved=True)
                save()
                print("Hosted missing-reference rejection passed; last good revision retained")
                return
            assert run["status"] == "succeeded", "Drawing run did not complete; inspect private workflow evidence"
            assert run["model_calls"] == 0
            revision = next(r for r in workspace["revisions"] if r["run_id"] == run["id"])
            assert current == revision["id"]
            assert revision["validation"]["identity"]["runtime"] == args.runtime
            document = revision["validation"]["drawings"]
            assert document["identity"] == revision["validation"]["identity"]
            assert {s["id"] for s in document["sheets"]} == {"plate_detail", "plate_section", "assembly_bom"}
            assert all(not s["issues"] for s in document["sheets"])
            detail = next(s for s in document["sheets"] if s["id"] == "plate_detail")
            measured = next(d for d in detail["dimensions"] if d["id"] == "width_tolerance")
            assert abs(measured["value"] - 8) < 1e-6
            assert any(d["kind"] == "diameter" and abs(d["value"] - 2) < 1e-6 for d in detail["dimensions"])
            assert detail["controls"][0]["conformance"] == "not_measured"
            assert revision["validation"]["bom"]["flat"][0]["quantity"] == 60
            assert revision["validation"]["assemblyPlacement"]["solidChecks"] == 60
            downloaded = {}
            for artifact in workspace["artifacts"]:
                if artifact["revision_id"] != revision["id"] or artifact["kind"] not in {"drawing", "bom"}:
                    continue
                signed = call("GET", "/api/artifacts/" + artifact["id"])
                target = urlparse(signed["url"])
                if target.hostname != "bisbakbhybkhcjztqnag.supabase.co":
                    raise ValueError("Unexpected private storage host")
                with httpx.Client(timeout=120) as files:
                    response = files.get(signed["url"])
                assert response.status_code == 200 and len(response.content) == artifact["bytes"]
                assert Path(artifact["name"]).name == artifact["name"]
                (folder / artifact["name"]).write_bytes(response.content)
                downloaded[artifact["name"]] = hashlib.sha256(response.content).hexdigest()
                if artifact["name"].endswith(".pdf"):
                    assert response.content.startswith(b"%PDF-")
            expected = {f"drawing-{s}.{e}" for s in ("plate_detail", "plate_section", "assembly_bom") for e in ("svg", "pdf", "dxf")}
            assert expected.issubset(downloaded)
            case.update(status="passed", revisionId=revision["id"], authenticatedDownloads=downloaded,
                        identity=revision["validation"]["identity"])
            state["revisionId"] = revision["id"]
            (folder / "workspace.json").write_text(json.dumps(workspace, indent=2))
            save()
            print("Hosted drawing job passed: 3 sheets, native dimensions/GD&T/section, 60-occurrence BOM, 9 private vector exports, zero model calls")
        elif args.action == "security":
            assert state["cases"]["positive"]["status"] == "passed"
            artifact = next(a for a in workspace["artifacts"] if a["revision_id"] == state["revisionId"]
                            and a["kind"] == "drawing" and a["name"].endswith(".pdf"))
            protection = httpx.Cookies()
            for cookie in cookies:
                if "vercel" in cookie["name"]:
                    protection.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
            with httpx.Client(base_url=args.url, cookies=protection, headers={"Origin": args.url}, timeout=60) as other:
                r = other.get("/api/artifacts/" + artifact["id"])
                assert r.status_code == 401
                demo = re.search(r"Email: `([^`]+)`.*Password: `([^`]+)`",
                                 (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8"))
                if not demo:
                    raise ValueError("Documented second-account fixture is unavailable")
                r = other.post("/api/auth/login", json={"email": demo.group(1), "password": demo.group(2)})
                assert r.status_code == 200 and r.json()["profile"]["id"] != workspace["project"]["owner_id"]
                assert other.get("/api/artifacts/" + artifact["id"]).status_code == 404
                assert other.get("/api/projects/" + args.project).status_code == 404
                bad = state["cases"]["positive"]["request"]
                assert other.post("/api/projects/" + args.project + "/drawings", json=bad).status_code == 404
            endpoint = "/api/projects/" + args.project + "/drawings"
            original = state["cases"]["positive"]["request"]
            repeated = web.post(endpoint, json=original)
            assert repeated.status_code == 202 and repeated.json()["runId"] == state["cases"]["positive"]["runId"]
            conflict = json.loads(json.dumps(original))
            conflict["sheets"][0]["title"] = "Conflicting reuse must be rejected"
            assert web.post(endpoint, json=conflict).status_code == 409
            stale = {**original, "idempotencyKey": str(uuid4())}
            assert web.post(endpoint, json=stale).status_code == 409
            invalid = {**original, "idempotencyKey": str(uuid4()), "sheets": []}
            assert web.post(endpoint, json=invalid).status_code == 400
            state["accessControls"] = {"anonymousDownload": 401, "otherOwnerDownload": 404,
                                       "otherOwnerProject": 404, "otherOwnerDrawingSubmission": 404,
                                       "exactRetrySameRun": True, "conflictingKey": 409, "staleRevision": 409,
                                       "malformedSheets": 400}
            save()
            print("Private drawing ownership passed: anonymous 401; other owner download, project and submission 404")
        elif args.action == "stable":
            assert current == state["revisionId"]
            assert len(revision["validation"]["drawings"]["sheets"]) == 3
            assert revision["validation"]["identity"]["runtime"] == args.runtime
            state["stableVerified"] = {"url": args.url, "revisionId": current, "time": time.time()}
            save()
            print("Stable site serves the verified native drawing revision")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--browser-state", type=Path, required=True)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--project", default="2c114144-7f36-4384-aa3c-58a9e66be79e")
    parser.add_argument("--runtime", default="forma-343517bf371ce748")
    parser.add_argument("--action", choices=["positive", "negative", "observe", "security", "stable"], required=True)
    parser.add_argument("--case", choices=["positive", "negative", "ui"], default="positive")
    try:
        main(parser.parse_args())
    except Exception as error:  # noqa: BLE001 - omit potentially credential-bearing HTTP diagnostics
        print("Drawing acceptance incomplete: " + type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
