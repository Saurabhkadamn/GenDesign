"""UI-only fixture check using real downloaded evidence and mocked API ownership."""
import argparse
import json
from pathlib import Path
import re
import subprocess


def main(args):
    def command(*values):
        print("Browser check: " + values[0], flush=True)
        result = subprocess.run([str(args.browser), "--session", "forma-assembly-release", *values],
            capture_output=True, text=True, timeout=60)
        if result.returncode: raise RuntimeError(result.stderr)
        return result.stdout
    report = json.loads((args.fixture / "report.json").read_text())
    report = {key: report[key] for key in ["identity", "requirements", "allRequirementsVerified", "bom", "nativeAssembly", "artifacts"]}
    snapshot = json.loads((args.fixture / "snapshot.json").read_text())
    # This client renders the occurrence tree and evidence, not mate inputs.
    # Keep the projection below Windows' command argument size limit.
    snapshot["manifest"]["references"] = []
    snapshot["manifest"]["joints"] = []
    project = {"id": "qualification", "owner_id": "fixture", "name": "60-part acceptance fixture",
        "current_revision_id": "revision", "created_at": "2026-10-01T07:00:00Z", "updated_at": "2026-10-01T07:00:00Z"}
    artifacts = [{"id": a["name"], "project_id": "qualification", "revision_id": "revision",
        "component_id": a["componentId"], "name": a["name"], "kind": a["kind"], "bytes": a["bytes"],
        "storage_path": "fixture/" + a["name"]} for a in report["artifacts"] if a["kind"] != "glb"]
    state = {"project": project, "revisions": [{"id": "revision", "project_id": "qualification", "run_id": "fixture",
        "ordinal": 1, "summary": "Hosted 60-part fixture", "manifest": snapshot["manifest"],
        "validation": report, "created_at": project["created_at"], "restored_from": None}],
        "messages": [], "runs": [], "artifacts": artifacts, "calculations": [], "events": []}
    profile = {"id": "fixture", "email": "fixture@example.invalid", "display_name": "Qualification engineer",
               "role": "engineer", "active": True, "must_change_password": False}
    for route, body in [("**/api/session", {"configured": True, "profile": profile}),
                        ("**/api/projects", [project]), ("**/api/projects/qualification", state)]:
        command("network", "unroute", route)
        command("network", "route", route, "--body", json.dumps(body, separators=(",", ":")))
    command("open", "http://127.0.0.1:3107")
    command("wait", "--text", "60-part acceptance fixture")
    view = command("snapshot", "-i")
    files = re.search(r'tab "Files 6"[^\n]+\bref=(e\d+)', view)
    if not files: raise RuntimeError("Six qualified artifact links missing")
    command("click", "@" + files.group(1))
    view = command("snapshot", "-i")
    reference = re.search(r'button "Bill of materials[^\n]+\[ref=(e\d+)\]', view)
    if not reference: raise RuntimeError("BOM action missing from Files")
    command("click", "@" + reference.group(1))
    command("wait", "--text", "Draft for engineering review")
    rows = json.loads(command("eval", "JSON.stringify(Array.from(document.querySelectorAll('table tbody tr')).map(r=>r.innerText))"))
    if isinstance(rows, str): rows = json.loads(rows)
    if len(rows) != 1 or "QUAL-PLATE" not in rows[0] or "60 ea" not in rows[0]:
        raise RuntimeError("Flat BOM did not display the qualified 60 occurrences")
    view = command("snapshot", "-i")
    structure = re.search(r'button "Assembly structure"[^\n]+\bref=(e\d+)', view)
    if not structure: raise RuntimeError("Structured BOM layout missing")
    command("click", "@" + structure.group(1))
    command("wait", "--text", "Quantities per parent assembly")
    errors = command("errors")
    if errors.strip(): raise RuntimeError("Browser console errors: " + errors)
    command("screenshot", str(args.fixture.parent / "bom-ui.png"))
    print(command("snapshot", "-i"))
    print("BOM UI passed: 60 occurrences, both table layouts, six artifact links, no browser errors.")
    print("Mocked API only; runtime and private artifact downloads are qualified separately.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    main(parser.parse_args())
