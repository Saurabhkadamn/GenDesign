"""Vercel sandbox execution contracts. Generated code never runs in the API."""
import asyncio
import hashlib
import io
import json
import os
import re
import tarfile
from typing import Protocol


from .config import settings

MAX_FILE = 40 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def normalize_python_source(source: str) -> str:
    """Repair a provider's extra JSON escaping without rewriting valid Python.

    Some tool providers occasionally return a complete module with literal
    ``\\n`` sequences instead of line breaks after nested JSON decoding. Only
    normalize when the text has no real line breaks and contains several escape
    sequences; ordinary Python string literals remain untouched.
    """
    if "\n" not in source and source.count("\\n") >= 2:
        return source.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    return source


def identity(snapshot, requirements):
    return {"candidate": digest(snapshot), "requirements": digest(requirements), "runtime": settings().runtime_version}


class ExecutionFailure(Exception):
    pass


class Executor(Protocol):
    async def create(self, name: str, lifetime: int = 1800) -> str: ...
    async def stage(self, name: str, files: dict[str, bytes]) -> None: ...
    async def execute(self, name: str, operation: str, timeout: int, path: str = "") -> dict: ...
    async def inspect(self, name: str) -> dict: ...
    async def read(self, name: str, filename: str) -> bytes: ...
    async def cancel(self, name: str) -> None: ...
    async def destroy(self, name: str) -> None: ...


def validate_files(files):
    if sum(len(data) for data in files.values()) > 120 * 1024 * 1024:
        raise ExecutionFailure("Workspace transfer exceeds 120 MB")
    for path, data in files.items():
        if len(data) > MAX_FILE or ".." in path or "//" in path or not re.fullmatch(r"(manifest|requirements|identity)\.json|[A-Za-z][A-Za-z0-9_-]{0,63}\.step|(parts|assemblies|calculations)/[A-Za-z0-9_/-]+\.py", path):
            raise ExecutionFailure("Invalid workspace transfer")


def filename(value):
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*\.(step|glb|json)", value):
        raise ExecutionFailure("Invalid artifact filename")
    return value


class VercelExecutor:
    async def box(self, name):
        from vercel import sandbox
        box = await sandbox.get_sandbox(name=name)
        if box.current_session is None or box.current_session.status != sandbox.SandboxStatus.RUNNING:
            raise ExecutionFailure("The build environment expired or stopped. Continue to create a fresh environment.")
        return box

    async def command(self, box, args, timeout=30):
        result = await box.run_process("/opt/forma/.venv/bin/python", ["-I", "/opt/forma/control.py", *args],
                                      cwd="/", sudo=True, kill_after=timeout, check=True, capture_output=True)
        return json.loads(result.stdout)

    async def create(self, name, lifetime=1800):
        from vercel import sandbox
        snapshot = os.getenv("CAD_RUNTIME_SNAPSHOT_ID")
        if not snapshot:
            raise ExecutionFailure("Hosted CAD runtime is not configured")
        box, _created = await sandbox.get_or_create_sandbox(name=name, resume=False, source=sandbox.SnapshotSource(snapshot_id=snapshot),
            execution_time_limit=lifetime, persistent=False, network_policy=sandbox.NetworkPolicy.deny_all(), ports=[],
            resources=sandbox.SandboxResources(vcpus=2))
        # The SDK's filesystem transport starts in this fixed directory, even for
        # snapshots created by an earlier runtime whose home directory differs.
        await box.run_process("mkdir", ["-p", "/vercel/sandbox"], cwd="/", sudo=True, check=True)
        return box.name

    async def stage(self, name, files):
        validate_files(files)
        box = await self.box(name)
        await self.command(box, ["prepare"])
        archive = io.BytesIO()
        with tarfile.open(fileobj=archive, mode="w") as tar:
            for path, data in files.items():
                entry = tarfile.TarInfo(path)
                entry.size, entry.mode, entry.uid, entry.gid = len(data), 0o444, 0, 0
                tar.addfile(entry, io.BytesIO(data))
        # The archive is constructed here from validated regular files. No caller tar is accepted.
        await box.fs.write_bytes("/tmp/forma-stage.tar", archive.getvalue())
        await box.run_process("tar", ["--extract", "--file=/tmp/forma-stage.tar", "--directory=/job/workspace", "--no-same-owner"], cwd="/", sudo=True, check=True)
        await box.run_process("rm", ["-f", "/tmp/forma-stage.tar"], cwd="/", sudo=True, check=True)

    async def execute(self, name, operation, timeout, path=""):
        return await self.command(await self.box(name), ["execute", "--operation", operation, "--timeout", str(timeout), "--path", path], timeout + 15)

    async def inspect(self, name):
        return await self.command(await self.box(name), ["inspect"])

    async def read(self, name, value):
        box = await self.box(name)
        path = f"/job/output/{filename(value)}"
        probe = await box.run_process("/opt/forma/.venv/bin/python", ["-I", "-c",
            "import os,stat,sys; s=os.lstat(sys.argv[1]); assert stat.S_ISREG(s.st_mode) and 0<s.st_size<=41943040; print(s.st_size)", path], cwd="/", sudo=True, check=True, capture_output=True)
        content = await box.fs.read_bytes(path)
        if len(content) != int(probe.stdout) or len(content) > MAX_FILE:
            raise ExecutionFailure("Output changed after execution")
        return content

    async def cancel(self, name):
        await self.command(await self.box(name), ["cancel"])

    async def destroy(self, name):
        from vercel import sandbox
        box = await sandbox.get_sandbox(name=name)
        await box.destroy()


def executor() -> Executor:
    return VercelExecutor()


def build_error(receipt, stage):
    diagnostic = receipt.get("diagnostic", "")[-6000:]
    location = re.findall(r'File "(?:/job/workspace/)?([^"\n]+\.py)", line (\d+)', diagnostic)
    workspace_frames = [frame for frame in location if frame[0].startswith(("parts/", "assemblies/", "calculations/"))]
    location = workspace_frames or location
    category, guidance = "geometry", "Inspect the failing operation and repair the candidate before rebuilding."
    if receipt.get("timedOut"):
        category, guidance = "timeout", "Simplify the operation. The environment was discarded."
    elif "Cannot find a solid" in diagnostic:
        category, guidance = "fillet_without_solid", "Create the solid first; apply the corner fillet to vertical solid edges before drilling holes."
    elif "not callable" in diagnostic or "Expected" in diagnostic and "found" in diagnostic:
        category, guidance = "edge_selector", "Use CadQuery selectors such as edges('|Z') or a Selector object. Lists and Python expressions are not selector strings."
    elif "SyntaxError" in diagnostic or "IndentationError" in diagnostic:
        category, guidance = "python_syntax", "Fix Python syntax at the reported line."
    elif "FileNotFoundError" in diagnostic:
        category, guidance = "missing_source", (
            "Make every manifest component reference an existing source under parts/ or assemblies/. "
            "Calculation files cannot be geometry components."
        )
    error = {"stage": stage, "category": category, "location": {"file": location[-1][0], "line": int(location[-1][1])} if location else None,
             "guidance": guidance, "diagnostic": diagnostic}
    error["fingerprint"] = digest({"category": category, "location": error["location"], "lastLine": diagnostic.strip().splitlines()[-1:]})
    return error
