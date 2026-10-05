"""Reviewed diagnostic fixtures in an isolated, unprivileged hosted CAD worker."""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


async def main(args):
    runtime = json.loads(args.runtime.read_text())
    auth = json.loads(args.cli_auth_file.read_text())
    os.environ.update(VERCEL_TOKEN=auth["token"], VERCEL_PROJECT_ID="prj_eHyoZK7vTY2f6bbySwO8StXYAqHs",
        VERCEL_TEAM_ID="team_Nzdcjz2LikTu8cDMJVaMiX45", CAD_RUNTIME_SNAPSHOT_ID=runtime["snapshotId"],
        CAD_RUNTIME_VERSION=runtime["runtimeVersion"])
    from forma_api.execution import executor, identity, build_error
    from forma_api.contracts import Snapshot

    worker = executor()
    names = []
    evidence_path = args.report_dir / "diagnostic-acceptance.json"
    evidence = json.loads(evidence_path.read_text()) if args.positive_only else {"runtime": runtime, "cases": {}}
    assert evidence["runtime"] == runtime
    args.report_dir.mkdir(parents=True, exist_ok=True)
    def save():
        (args.report_dir / "diagnostic-acceptance.json").write_text(json.dumps(evidence, indent=2) + "\n")
    def snapshot(source):
        return Snapshot.model_validate({"manifest": {"rootComponentId": "housing", "components": [{
            "id": "housing", "name": "Diagnostic fixture", "source": "parts/housing.py", "kind": "solid"}]},
            "files": {"parts/housing.py": source}}).model_dump()
    def transfer(candidate):
        return {"manifest.json": json.dumps(candidate["manifest"]).encode(), "requirements.json": b"[]",
            "identity.json": json.dumps(identity(candidate, [])).encode(),
            **{name: text.encode() for name, text in candidate["files"].items()}}
    cases = {
        "wire_fillet": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').rect(80,50).vertices().fillet(3)\n",
        "invalid_overlap": "import cadquery as cq\ndef build(p,d): return cq.Workplane('XY').pushPoints([(0,0),(34,0)]).circle(19.05).extrude(26.06)\n",
        "native_signal": "import os,signal\ndef build(p,d): os.kill(os.getpid(),signal.SIGSEGV)\n",
        "silent_kill": "import os,signal\ndef build(p,d): os.kill(os.getpid(),signal.SIGKILL)\n",
    }
    expected_categories = {"wire_fillet": "fillet_without_solid", "invalid_overlap": "invalid_component",
                           "native_signal": "worker_signal", "silent_kill": "worker_signal"}
    try:
        name = "forma-diagnostic-" + uuid4().hex
        names.append(name)
        evidence["sandboxName"] = name
        save()
        await worker.create(name)
        for key, source in ({} if args.positive_only else cases).items():
            candidate = snapshot(source)
            await worker.stage(name, transfer(candidate))
            receipt = await worker.execute(name, "build", 90)
            error = build_error(receipt, "build")
            evidence["cases"][key] = {"receipt": receipt, "error": error}
            save()
            assert receipt["exitCode"] != 0 and receipt["clean"] and not receipt["timedOut"]
            assert receipt["identity"] == identity(candidate, [])
            assert error["category"] == expected_categories[key] and error["diagnostic"]
            if key == "native_signal":
                assert "SIGSEGV" in receipt["diagnostic"] and 'parts/housing.py' in receipt["diagnostic"]
            if key == "silent_kill":
                assert receipt["exitCode"] == -9 and "No traceback was emitted" in receipt["diagnostic"]
            try:
                await worker.read(name, "housing.step")
            except Exception:
                pass
            else:
                raise AssertionError("Failed fixture emitted a STEP")
        source = """import cadquery as cq
def build(p,d):
    a=cq.Workplane('XY').circle(19.05).extrude(26.06).translate((0,0,-3))
    b=cq.Workplane('XY').center(34,0).circle(19.05).extrude(26.06).translate((0,0,-3))
    return cq.Workplane('XY').box(108,68,20.06,centered=(True,True,False)).translate((17,0,0)).cut(a.union(b))
"""
        candidate = snapshot(source)
        data = transfer(candidate)
        await worker.stage(name, data)
        receipt = await worker.execute(name, "build", 90)
        evidence["cases"]["repaired_cavity"] = {"receipt": receipt}
        save()
        assert receipt["exitCode"] == 0 and receipt["clean"]
        step = await worker.read(name, "housing.step")
        (args.report_dir / "fixture-housing.step").write_bytes(step)
        validator = "forma-diagnostic-" + uuid4().hex
        names.append(validator)
        await worker.create(validator)
        await worker.stage(validator, {k: v for k, v in data.items() if not k.endswith('.py')} | {"housing.step": step})
        checked = await worker.execute(validator, "validate", 90)
        evidence["cases"]["repaired_cavity"]["validationReceipt"] = checked
        save()
        assert checked["exitCode"] == 0 and checked["clean"]
        report = json.loads(await worker.read(validator, "report.json"))
        assert report["identity"] == identity(candidate, [])
        assert report["components"]["housing"]["valid"]
        assert abs(report["components"]["housing"]["dimensions"][2] - 20.06) < 1e-6
        evidence["cases"]["repaired_cavity"] = {"receipt": receipt, "validation": report}
        evidence["status"] = "passed"
        save()
        print(json.dumps({"status": "passed", "cases": list(evidence["cases"])}))
    finally:
        for name in names:
            try:
                await worker.destroy(name)
            except Exception:
                pass


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--positive-only", action="store_true")
    for name in ["runtime", "cli-auth-file", "report-dir"]:
        parser.add_argument("--" + name, type=Path, required=True)
    try:
        asyncio.run(main(parser.parse_args()))
    except Exception as error:
        print("Diagnostic acceptance incomplete: " + type(error).__name__, file=sys.stderr)
        import traceback
        for frame in traceback.extract_tb(error.__traceback__)[-4:]:
            print(f"  {Path(frame.filename).name}:{frame.lineno} {frame.name}", file=sys.stderr)
        raise SystemExit(1)
