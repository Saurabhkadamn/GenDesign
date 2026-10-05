"""Build a pinned native/Python image and qualify it before snapshot publication."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import traceback
import xml.etree.ElementTree as ET

import httpx
from dotenv import dotenv_values
from vercel import sandbox
from vercel.api import session
from vercel.sandbox import SandboxCredentials, SandboxServiceOptions

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "runtimes/python"
SOURCES = ["uv.lock", "pyproject.toml", "forma_runtime.py", "requirements_check.py",
           "geometry_inspection.py", "control.py", "assembly_state.py", "native_assembly.py",
           "bom.py", "drawings.py", "drawing_export.py", "runtime_identity.py"]
JSON_HASH = "aaf127c04cb31c406e5b04a63f1ae89369fccde6d8fa7cdda1ed4f32dfc5de63"


async def main(args):
    previous = dotenv_values(args.env_file)["CAD_RUNTIME_SNAPSHOT_ID"]
    report_dir = args.report_dir.resolve()
    report_dir.mkdir(parents=True, exist_ok=True)
    state_path = report_dir / "snapshot-build-state.json"
    source_paths = [RUNTIME / n for n in SOURCES]
    source_paths += sorted((ROOT / "runtimes/native").glob("*"))
    source_paths += sorted((RUNTIME / "tests").glob("*.py"))
    inputs = {p.relative_to(ROOT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in source_paths if p.is_file()}
    source_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    state = json.loads(state_path.read_text()) if args.resume and state_path.exists() else {
        "sourceHash": source_hash, "completed": [], "name": args.name}
    if state["sourceHash"] != source_hash:
        raise ValueError("Build sources changed; use a new report directory and name")
    if state_path.exists() and not args.resume:
        raise ValueError("A build already exists; inspect its state and resume it")
    header = args.json_header.read_bytes()
    if hashlib.sha256(header).hexdigest() != JSON_HASH:
        raise ValueError("JSON dependency hash mismatch")
    options = []
    if args.cli_auth_file:
        token = json.loads(args.cli_auth_file.read_text())["token"]
        async def credentials():
            return SandboxCredentials(token=token, project_id=args.project_id, team_id=args.team_id)
        options = [SandboxServiceOptions(credentials_factory=credentials)]
    allow = {h: [] for h in ["archive.ubuntu.com", "security.ubuntu.com", "ports.ubuntu.com",
                             "*.archive.ubuntu.com", "pypi.org", "files.pythonhosted.org"]}
    def save(): state_path.write_text(json.dumps(state, indent=2) + "\n")
    async with session(service_options=options, httpx_client_factory=lambda: httpx.AsyncClient(
        timeout=httpx.Timeout(connect=30, read=900, write=120, pool=30))):
        if args.resume:
            box = await sandbox.get_sandbox(name=state["name"], project_id=args.project_id)
        else:
            box = await sandbox.create_sandbox(name=args.name, project_id=args.project_id,
                source=sandbox.SnapshotSource(snapshot_id=previous), persistent=True, destroy=False,
                execution_time_limit=1800, ports=[], resources=sandbox.SandboxResources(vcpus=4),
                network_policy=sandbox.NetworkPolicy.custom(allow=allow))
            state.update(name=args.name, status="building")
            save()
        async def command(stage, executable, arguments, *, cwd="/", env=None, limit=900):
            if stage in state["completed"]:
                return
            print("Image stage: " + stage, flush=True)
            remote_log = "/tmp/forma-qualification-" + stage + ".log"
            pending = state.get("pendingCommand")
            if pending:
                if pending["stage"] != stage or pending["sessionId"] != box.current_session_id:
                    raise RuntimeError("Pending command does not match this build stage/session")
                process = await box.get_process(pending["id"])
            else:
                # Keep output in a file: a stalled streaming observer must not
                # hide a terminal command or cause it to be replayed on resume.
                wrapper = (
                    "import subprocess,sys; "
                    "f=open(sys.argv[1],'wb'); "
                    "r=subprocess.run(sys.argv[2:],stdout=f,stderr=subprocess.STDOUT); "
                    "f.close(); sys.exit(r.returncode)"
                )
                process = await box.create_process("/usr/bin/python3",
                    ["-c", wrapper, remote_log, executable, *arguments],
                    cwd=cwd, env=env, sudo=True, kill_after=limit,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                state["pendingCommand"] = {"stage": stage, "id": process.id,
                    "sessionId": box.current_session_id}
                save()
            while process.returncode is None:
                try:
                    process = await asyncio.wait_for(box.get_process(process.id, wait=True), 45)
                except TimeoutError:
                    print("Image stage still being observed: " + stage, flush=True)
            log = await box.fs.read_text(remote_log, cwd="/")
            (report_dir / (stage + ".log")).write_text(log, encoding="utf-8")
            if process.returncode:
                state.update(status="failed", failedStage=stage, exitCode=process.returncode)
                save()
                print(log[-2500:], flush=True)
                raise RuntimeError("Image stage failed: " + stage)
            state["completed"].append(stage)
            state.pop("pendingCommand", None)
            save()
        try:
            # A previously qualified base can retain its build tree. Reset
            # only this factory's directory in the new isolated VM.
            await command("fresh-build-directory", "/usr/bin/python3", ["-c",
                "from pathlib import Path; import shutil; p=Path('/tmp/forma-build'); assert p.resolve()==p; shutil.rmtree(p) if p.exists() else None"])
            await command("directories", "mkdir", ["-p", "/vercel/sandbox", "/tmp/forma-build/json",
                "/qualification/runtimes/python/tests", "/qualification/fixtures", "/opt/forma/native"])
            if "upload" not in state["completed"]:
                await box.fs.write_bytes("/tmp/forma-build/ondsel.bundle", args.ondsel_bundle.read_bytes(), cwd="/")
                await box.fs.write_bytes("/tmp/forma-build/json/json.hpp", header, cwd="/")
                await box.fs.write_bytes("/tmp/forma-build/json/LICENSE", args.json_license.read_bytes(), cwd="/")
                for name in SOURCES:
                    await box.fs.write_bytes("/tmp/forma-build/" + name, (RUNTIME / name).read_bytes(), cwd="/")
                    await command("install-" + name.replace(".", "-"), "install", ["-m", "644", "/tmp/forma-build/" + name, "/opt/forma/" + name])
                    await box.fs.write_bytes("/qualification/runtimes/python/" + name, (RUNTIME / name).read_bytes(), cwd="/")
                for path in (ROOT / "runtimes/native").glob("*"):
                    if path.is_file(): await box.fs.write_bytes("/tmp/forma-build/" + path.name, path.read_bytes(), cwd="/")
                for path in (RUNTIME / "tests").glob("*.py"):
                    await box.fs.write_bytes("/qualification/runtimes/python/tests/" + path.name, path.read_bytes(), cwd="/")
                await box.fs.write_bytes("/qualification/fixtures/native-fourbar.json", (ROOT / "fixtures/native-fourbar.json").read_bytes(), cwd="/")
                state["completed"].append("upload")
                save()
            await command("apt-https", "/usr/bin/python3", ["-c", "from pathlib import Path; paths=list(Path('/etc/apt/sources.list.d').glob('*.sources'))+list(Path('/etc/apt/sources.list.d').glob('*.list')); paths += [Path('/etc/apt/sources.list')] if Path('/etc/apt/sources.list').is_file() else []; [p.write_text(p.read_text().replace('http://','https://')) for p in paths]"])
            await command("apt-index-https", "apt-get", ["-o", "APT::Update::Error-Mode=any", "update"])
            await command("build-tools", "apt-get", ["install", "-y", "--no-install-recommends", "g++", "cmake", "git", "make"], env={"DEBIAN_FRONTEND": "noninteractive"})
            await command("python-sync", "/usr/local/bin/uv", ["sync", "--locked", "--python", "/opt/forma/.venv/bin/python"], cwd="/opt/forma", env={"UV_CACHE_DIR": "/tmp/forma-uv-cache"})
            await command("solver-source", "git", ["clone", "/tmp/forma-build/ondsel.bundle", "/tmp/forma-build/ondsel"])
            await command("native-configure", "cmake", ["-S", "/tmp/forma-build", "-B", "/tmp/forma-build/build", "-DCMAKE_BUILD_TYPE=Release", "-DCMAKE_INSTALL_PREFIX=/opt/forma/native", "-DFORMA_ONDSEL_SOURCE_DIR=/tmp/forma-build/ondsel", "-DFORMA_JSON_INCLUDE_DIR=/tmp/forma-build/json"])
            await command("native-compile", "cmake", ["--build", "/tmp/forma-build/build", "--parallel", "4"], limit=1200)
            await command("native-install", "cmake", ["--install", "/tmp/forma-build/build"])
            await command("json-notice", "install", ["-m", "644", "/tmp/forma-build/json/LICENSE", "/opt/forma/native/notices/nlohmann-json-LICENSE"])
            await command("solver-source-notice", "install", ["-m", "644", "/tmp/forma-build/ondsel.bundle", "/opt/forma/native/notices/OndselSolver-source.bundle"])
            await box.update_network_policy(sandbox.NetworkPolicy.deny_all())
            await command("runtime-tests", "/opt/forma/.venv/bin/python", ["-m", "pytest", "/qualification/runtimes/python/tests", "-q", "--tb=short", "--junitxml=/qualification/runtime-tests.xml"],
                env={"FORMA_NATIVE_TEST_BINARY": "/opt/forma/native/forma-assembly", "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2"}, limit=300)
            xml = await box.fs.read_bytes("/qualification/runtime-tests.xml", cwd="/")
            (report_dir / "runtime-tests.xml").write_bytes(xml)
            suites = list(ET.fromstring(xml).iter("testsuite"))
            if any(int(s.get("skipped", "0")) or int(s.get("failures", "0")) or int(s.get("errors", "0")) for s in suites):
                raise RuntimeError("Native release qualification requires zero skipped or failed tests")
            await command("root-ownership", "chown", ["-R", "root:root", "/opt/forma"])
            await command("attest", "/opt/forma/.venv/bin/python", ["-I", "/opt/forma/runtime_identity.py", "attest"])
            await command("supervisor-prepare", "/opt/forma/.venv/bin/python",
                ["-I", "/opt/forma/control.py", "prepare"])
            prepared = json.loads((report_dir / "supervisor-prepare.log").read_text())
            if not prepared["ready"]: raise RuntimeError("Supervisor is not ready")
            (report_dir / "engine-identity.json").write_bytes(await box.fs.read_bytes("/opt/forma/native/engine-identity.json", cwd="/"))
            saved = await box.snapshot()
            result = {"snapshotId": saved.id, "runtimeVersion": prepared["runtimeVersion"], "sourceHash": source_hash,
                      "qualificationTests": sum(int(s.get("tests", "0")) for s in suites)}
            (report_dir / "qualified-runtime-snapshot.json").write_text(json.dumps(result, indent=2) + "\n")
            state.update(status="qualified", **result)
            state.pop("failedStage", None)
            state.pop("exitCode", None)
            save()
            print("Qualified snapshot saved; production settings are unchanged.", flush=True)
            await box.stop()
        except BaseException:
            save()
            # Keep the named build for a confirmed resume within its bounded lifetime.
            raise


def arguments():
    parser = argparse.ArgumentParser()
    for name in ["env-file", "report-dir", "ondsel-bundle", "json-header", "json-license"]:
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--cli-auth-file", type=Path)
    parser.add_argument("--project-id", default="prj_eHyoZK7vTY2f6bbySwO8StXYAqHs")
    parser.add_argument("--team-id", default="team_Nzdcjz2LikTu8cDMJVaMiX45")
    parser.add_argument("--name", default="forma-native-release-20261001")
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    try:
        asyncio.run(main(arguments()))
    except Exception as error:
        print("Snapshot qualification did not complete: " + type(error).__name__, file=sys.stderr)
        if isinstance(getattr(error, "status_code", None), int):
            print("Sandbox response status: " + str(error.status_code), file=sys.stderr)
        code = getattr(error, "code", None)
        if isinstance(code, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", code):
            print("Sandbox response code: " + code, file=sys.stderr)
        for frame in traceback.extract_tb(error.__traceback__):
            print(f"  {frame.filename}:{frame.lineno} {frame.name}", file=sys.stderr)
        raise SystemExit(1)
