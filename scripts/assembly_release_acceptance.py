"""Reviewed 60-occurrence corpus through real hosted build/fresh-validator workers."""
import argparse
import asyncio
import csv
import io
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))


def fixture(thickness):
    frame = lambda position: {"position": position, "rotation": [0, 0, 0]}
    positions = [[(i % 10) * 12, (i // 10) * 12, 0] for i in range(60)]
    instances = [{"id": f"plate_{i}", "definitionId": "plate", "name": f"Plate {i + 1}",
                  "frame": frame(p if i == 0 else [p[0] + 1, p[1] + 2, p[2] + 3])}
                 for i, p in enumerate(positions)]
    refs = [{"id": "part_origin", "componentId": "plate", "kind": "datum", "frame": frame([0, 0, 0])}]
    refs += [{"id": f"grid_{i}", "componentId": "plate", "kind": "placement_datum", "frame": frame(p)}
             for i, p in enumerate(positions) if i]
    manifest = {"rootComponentId": "assembly", "components": [
        {"id": "plate", "name": "Qualification plate", "source": "parts/plate.py", "kind": "solid",
         "parameters": {"thickness": thickness}, "partMetadata": {"partNumber": "QUAL-PLATE", "revision": "TEST", "description": "Acceptance fixture only"}},
        {"id": "assembly", "name": "60-part acceptance fixture", "source": "assemblies/grid.py", "kind": "assembly",
         "dependencies": ["plate"], "parameters": {"positions": positions}}],
        "instances": instances, "references": refs,
        "joints": [{"id": f"mate_{i}", "kind": "fixed", "referenceA": f"grid_{i}", "referenceB": "part_origin",
                    "occurrenceA": "plate_0", "occurrenceB": f"plate_{i}"} for i in range(1, 60)],
        "nativeAssembly": {"groundedInstances": ["plate_0"], "allowedDof": 0}}
    files = {"parts/plate.py": "import cadquery as cq\ndef build(parameters, dependencies):\n    return cq.Workplane('XY').box(8, 8, parameters['thickness']).faces('>Z').workplane().hole(2)\n",
             "assemblies/grid.py": "import cadquery as cq\ndef build(parameters, dependencies):\n    result = cq.Assembly(name='assembly')\n    for i, p in enumerate(parameters['positions']):\n        result.add(dependencies['plate'], name=f'plate_{i}', loc=cq.Location(cq.Vector(*p)))\n    return result\n"}
    return {"manifest": manifest, "files": files}


async def main(args):
    runtime = json.loads(args.runtime.read_text())
    token = json.loads(args.cli_auth_file.read_text())["token"]
    os.environ.update(VERCEL_TOKEN=token, VERCEL_TEAM_ID=args.team_id, VERCEL_PROJECT_ID=args.project_id,
                      CAD_RUNTIME_SNAPSHOT_ID=runtime["snapshotId"], CAD_RUNTIME_VERSION=runtime["runtimeVersion"])
    os.environ.pop("VERCEL_OIDC_TOKEN", None)
    from forma_api.contracts import Snapshot
    from forma_api.execution import SandboxExpired, VercelExecutor, identity
    executor = VercelExecutor()
    folder = args.report_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    record_path = folder / "acceptance-state.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {"runtimeVersion": runtime["runtimeVersion"], "cases": {}}
    if record["runtimeVersion"] != runtime["runtimeVersion"]: raise ValueError("Use a new evidence directory for a different runtime")
    def save(): record_path.write_text(json.dumps(record, indent=2) + "\n")
    for key, thickness, negative in [("sixty-parts-2mm", 2, False), ("sixty-parts-4mm", 4, False),
                                      ("contradictory-grounded", 2, True)]:
        if record["cases"].get(key, {}).get("status") == "passed": continue
        if key in record["cases"]:
            prior = record["cases"][key]
            if not args.retry_preparation or prior.get("buildReceipt"):
                raise ValueError("An incomplete case exists; inspect its recorded workers before repeating it")
            for worker in prior["workers"]:
                try:
                    await executor.is_running(worker)
                except SandboxExpired:
                    continue
                raise ValueError("A worker is still running; inspect it instead of repeating preparation")
            record.setdefault("preparationFailures", []).append({"case": key, **prior})
            del record["cases"][key]
            save()
        source = fixture(thickness)
        if negative: source["manifest"]["nativeAssembly"]["groundedInstances"].append("plate_1")
        snapshot = Snapshot.model_validate(source).model_dump()
        requirements = [{"id": "inventory", "description": "60 physical parts", "kind": "solid_count", "count": 60}]
        expected = identity(snapshot, requirements)
        encode = lambda value: json.dumps(value).encode()
        metadata = {"manifest.json": encode(snapshot["manifest"]), "requirements.json": encode(requirements), "identity.json": encode(expected)}
        names = [f"forma-release-{key}-build", f"forma-release-{key}-validate"]
        case = record["cases"][key] = {"status": "running", "workers": names, "identity": expected}
        save()
        started = time.monotonic()
        print("Hosted supervisor acceptance: " + key, flush=True)
        try:
            await executor.create(names[0], lifetime=600)
            await executor.stage(names[0], {**metadata, **{p: s.encode() for p, s in snapshot["files"].items()}})
            receipt = await executor.execute(names[0], "build", 300)
            case["buildReceipt"] = receipt
            save()
            if negative:
                assert receipt["exitCode"] != 0 and receipt["clean"] and not receipt["timedOut"]
                assert receipt["identity"] == expected
                assert any(reason in receipt["diagnostic"] for reason in ["grounded occurrence", "residual", "native assembly worker"])
                case.update(status="passed", expectedRejection=True, elapsedSeconds=time.monotonic() - started)
                save()
                print(key + " passed: conflicting grounded mate rejected before acceptance", flush=True)
                continue
            if receipt["exitCode"] or receipt["timedOut"] or not receipt["clean"] or receipt["identity"] != expected:
                raise ValueError("Unprivileged build failed; inspect saved diagnostic")
            steps = {c["id"] + ".step": await executor.read(names[0], c["id"] + ".step") for c in snapshot["manifest"]["components"]}
            await executor.create(names[1], lifetime=600)
            await executor.stage(names[1], {**metadata, **steps})
            receipt = await executor.execute(names[1], "validate", 300)
            case["validationReceipt"] = receipt
            save()
            if receipt["exitCode"] or receipt["timedOut"] or not receipt["clean"] or receipt["identity"] != expected:
                raise ValueError("Fresh STEP validation failed; inspect saved diagnostic")
            report = json.loads(await executor.read(names[1], "report.json"))
            assert report["identity"] == expected
            assert report["nativeAssembly"]["degreesOfFreedom"] == 0
            assert report["assemblyPlacement"]["solidChecks"] == 60
            assert len(report["assemblyPlacement"]["validatedOccurrences"]) == 60
            assert report["bom"]["flat"][0]["quantity"] == 60
            assert report["bom"]["status"] == "draft"
            assert report["allRequirementsVerified"]
            case_folder = folder / key
            case_folder.mkdir(exist_ok=True)
            (case_folder / "report.json").write_text(json.dumps(report, indent=2))
            (case_folder / "snapshot.json").write_text(json.dumps(snapshot, indent=2))
            for artifact in report["artifacts"]:
                content = await executor.read(names[1], artifact["name"])
                assert len(content) == artifact["bytes"]
                (case_folder / artifact["name"]).write_bytes(content)
            rows = list(csv.DictReader(io.StringIO((case_folder / "bom-flat.csv").read_text(encoding="utf-8-sig"))))
            assert len(rows) == 1 and int(rows[0]["quantity"]) == 60
            # Reopen the downloaded assembly locally using an analytic volume oracle
            # in a separate CAD environment; see --verify-local.
            case.update(status="passed", elapsedSeconds=time.monotonic() - started, solidChecks=60,
                        bomQuantity=60, expectedVolumeMm3=60 * (64 - math.pi) * thickness)
            save()
            print(key + " passed: native solve, fresh STEP, 60 placements, BOM and artifact reads", flush=True)
        except Exception:
            case["status"] = "failed"
            save()
            raise
        finally:
            for name in names:
                try: await executor.destroy(name)
                except Exception: pass


def verify_local(folder):
    import cadquery as cq
    record = json.loads((folder / "acceptance-state.json").read_text())
    for key, case in record["cases"].items():
        if case.get("status") != "passed" or case.get("expectedRejection"): continue
        shape = cq.importers.importStep(str(folder / key / "assembly.step")).val()
        assert shape.isValid() and len(shape.Solids()) == 60
        error = abs(shape.Volume(tol=1e-9) - case["expectedVolumeMm3"])
        assert error < 1e-3, "Downloaded STEP disagrees with analytic geometry"
        case["downloadedStepVolumeErrorMm3"] = error
        print(key, "downloaded STEP oracle passed", error)
    (folder / "acceptance-state.json").write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--cli-auth-file", type=Path)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--team-id", default="team_Nzdcjz2LikTu8cDMJVaMiX45")
    parser.add_argument("--project-id", default="prj_eHyoZK7vTY2f6bbySwO8StXYAqHs")
    parser.add_argument("--verify-local", action="store_true")
    parser.add_argument("--retry-preparation", action="store_true")
    args = parser.parse_args()
    if args.verify_local:
        verify_local(args.report_dir)
    else:
        asyncio.run(main(args))
