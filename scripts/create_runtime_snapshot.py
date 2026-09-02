"""Update the reviewed CAD image using the Python Sandbox SDK. Never run generated code here."""
import asyncio
import hashlib
import json
from pathlib import Path

from dotenv import load_dotenv
from vercel import sandbox

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/hosted-runtime.env")
FILES = ["uv.lock", "forma_runtime.py", "requirements_check.py", "control.py"]


async def main():
    import os
    previous = os.environ["CAD_RUNTIME_SNAPSHOT_ID"]
    version = "forma-" + hashlib.sha256(b"".join((ROOT / "runtimes/python" / name).read_bytes() for name in FILES)).hexdigest()[:16]
    box = await sandbox.create_sandbox(source=sandbox.SnapshotSource(snapshot_id=previous),
        persistent=False, execution_time_limit=900, network_policy=sandbox.NetworkPolicy.deny_all(), ports=[], resources=sandbox.SandboxResources(vcpus=2))
    try:
        for name in FILES:
            await box.fs.write_bytes(f"/tmp/{name}", (ROOT / "runtimes/python" / name).read_bytes())
            await box.run_process("cp", [f"/tmp/{name}", f"/opt/forma/{name}"], sudo=True, check=True)
            await box.run_process("chmod", ["644", f"/opt/forma/{name}"], sudo=True, check=True)
        await box.run_process("mkdir", ["-p", "/job"], sudo=True, check=True)
        probe = await box.run_process("/opt/forma/.venv/bin/python", ["-I", "/opt/forma/control.py", "prepare"], sudo=True, capture_output=True, check=True)
        assert json.loads(probe.stdout)["ready"]
        saved = await box.snapshot()
        (ROOT / "test-results/runtime-v2-snapshot.json").write_text(json.dumps({"snapshotId": saved.id, "runtimeVersion": version}, indent=2))
        print("Python CAD snapshot prepared and saved to test-results/runtime-v2-snapshot.json")
    finally:
        try:
            await box.stop()
            await box.destroy()
        except Exception:
            pass


asyncio.run(main())
