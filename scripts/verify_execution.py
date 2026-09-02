"""Reviewed fixtures executed remotely in Vercel Sandbox, without model calls."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/hosted-runtime.env")
runtime_config = json.loads((ROOT / "test-results/runtime-v2-snapshot.json").read_text())
os.environ["CAD_RUNTIME_SNAPSHOT_ID"] = runtime_config["snapshotId"]
os.environ["CAD_RUNTIME_VERSION"] = runtime_config["runtimeVersion"]
sys.path.insert(0, str(ROOT / "apps/api"))

from forma_api.execution import executor, identity, build_error
from forma_api.contracts import Snapshot
from forma_api.requirements import explicit_requirements

REQUEST = "Design an aluminum mounting plate, 80 × 50 × 6 mm, centered at the origin. Add four 6 mm diameter through-holes at X = ±30 mm and Y = ±15 mm. Round the four outer corners with a 3 mm radius."
SOURCE = """import cadquery as cq
def build(p, dependencies):
    return (cq.Workplane('XY').box(p['width'],p['height'],p['thickness'])
            .edges('|Z').fillet(p['radius']).faces('>Z').workplane()
            .pushPoints([(x,y) for x in (-30,30) for y in (-15,15)]).hole(p['diameter']))
"""
SNAPSHOT = Snapshot.model_validate({"manifest": {"schemaVersion": 1, "units": "mm", "rootComponentId": "plate", "instances": [],
    "components": [{"id": "plate", "name": "Aluminum mounting plate", "source": "parts/plate.py", "kind": "solid",
    "parameters": {"width": 80, "height": 50, "thickness": 6, "radius": 3, "diameter": 6}, "dependencies": [], "color": "#b9c4ad"}]},
    "files": {"parts/plate.py": SOURCE}}).model_dump()


async def main():
    runtime = executor()
    run = uuid4().hex
    names = []
    timings = []
    requirements = explicit_requirements(REQUEST)
    expected = identity(SNAPSHOT, requirements)
    data = {"manifest.json": json.dumps(SNAPSHOT["manifest"]).encode(), "requirements.json": json.dumps(requirements).encode(),
            "identity.json": json.dumps(expected).encode(), "parts/plate.py": SOURCE.encode()}
    try:
        box = f"forma-{run}-{uuid4().hex[:12]}"
        names.append(box)
        start = time.perf_counter()
        await runtime.create(box)
        cold_create = time.perf_counter()-start
        for attempt in range(3):
            start = time.perf_counter()
            await runtime.stage(box, data)
            preparation = time.perf_counter()-start + (cold_create if attempt == 0 else 0)
            result = await runtime.execute(box, "build", 180)
            assert result["exitCode"] == 0 and result["clean"] and result["identity"] == expected, result
            timings.append({"attempt": attempt+1, "preparationSeconds": preparation, "executionSeconds": result["elapsedMs"]/1000,
                            "totalSeconds": time.perf_counter()-start+(cold_create if attempt == 0 else 0)})
        step = await runtime.read(box, "plate.step")
        validator = f"forma-{run}-{uuid4().hex[:12]}"
        names.append(validator)
        await runtime.create(validator)
        await runtime.stage(validator, {k: v for k, v in data.items() if not k.endswith(".py")} | {"plate.step": step})
        result = await runtime.execute(validator, "validate", 180)
        assert result["exitCode"] == 0, result
        report = json.loads(await runtime.read(validator, "report.json"))
        assert report["identity"] == expected and report["allRequirementsVerified"], report
        assert (await runtime.read(validator, "preview.glb"))[:4] == b"glTF"
        # A clean next attempt cannot see the old STEP or silently validate stale output.
        await runtime.stage(box, {**data, "parts/plate.py": b"def build(p,d):\n raise ValueError('fixture failure')\n"})
        result = await runtime.execute(box, "build", 180)
        assert result["exitCode"] != 0 and result["clean"]
        try:
            await runtime.read(box, "plate.step")
        except Exception:
            pass
        else:
            raise AssertionError("Stale STEP survived workspace cleanup")
        repairs = []
        for broken in ("return cq.Workplane('XY').rect(80,50).vertices().fillet(3)",
                       "return cq.Workplane('XY').box(80,50,6).edges('x>39').fillet(3)"):
            await runtime.stage(box, {**data, "parts/plate.py": ("import cadquery as cq\ndef build(p,d):\n " + broken + "\n").encode()})
            result = await runtime.execute(box, "build", 180)
            assert result["exitCode"] != 0 and result["clean"]
            repairs.append(build_error(result, "build"))
        assert {r["category"] for r in repairs} == {"fillet_without_solid", "edge_selector"}, repairs
        await runtime.stage(box, data)
        assert (await runtime.execute(box, "build", 180))["exitCode"] == 0
        # Process timeout must kill remaining children. Production discards the timed-out box.
        timeout_source = b"import subprocess,time\ndef build(p,d):\n subprocess.Popen(['/opt/forma/.venv/bin/python','-c','import time;time.sleep(60)'])\n time.sleep(60)\n"
        await runtime.stage(box, {**data, "parts/plate.py": timeout_source})
        timed = await runtime.execute(box, "build", 2)
        assert timed["timedOut"] and timed["clean"]
        await runtime.cancel(box)
        evidence = {"backend": "python", "executor": "vercel", "identity": expected, "timings": timings,
                    "requirements": report["requirements"], "staleArtifactsRejected": True,
                    "repairFeedback": repairs, "repairedCandidateBuilt": True, "timeoutChildrenCleaned": True}
        (ROOT / "test-results/cloud-execution.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence))
    finally:
        for box in names:
            try:
                await runtime.destroy(box)
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())
