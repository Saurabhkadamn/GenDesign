"""Installed-engine attestation shared by image builder and root supervisor."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys

ENGINE_COMMIT = "4be80eef02a3486cda0d78f3ccbb308d207a9639"
FILES = ("uv.lock", "pyproject.toml", "forma_runtime.py", "requirements_check.py",
         "geometry_inspection.py", "control.py", "assembly_state.py", "native_assembly.py",
         "bom.py", "drawings.py", "drawing_export.py", "runtime_identity.py", "native/engine-identity.json")


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def packages():
    return dict(sorted((re.sub(r"[-_.]+", "-", d.metadata["Name"]).lower(), d.version)
                       for d in importlib.metadata.distributions()))


def attest(root):
    root = Path(root)
    native = root / "native"
    files = [native / "forma-assembly"]
    files += sorted(native.glob("lib*/libOndselSolver.so*"))
    files += sorted((native / "notices").glob("*"))
    if not files[0].is_file() or not any("libOndselSolver.so" in p.name for p in files):
        raise ValueError("Native executable and shared Ondsel library must be installed")
    result = {"schemaVersion": 1, "solverCommit": ENGINE_COMMIT,
              "python": platform.python_version(), "platform": sys.platform,
              "dependencies": packages(),
              "nativeFiles": {p.relative_to(root).as_posix(): sha(p) for p in files if p.is_file()}}
    (native / "engine-identity.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
    return result


def verify(root):
    root = Path(root)
    identity = json.loads((root / "native/engine-identity.json").read_text())
    if identity.get("schemaVersion") != 1 or identity.get("solverCommit") != ENGINE_COMMIT:
        raise ValueError("Installed native identity is not a reviewed engine")
    if identity.get("python") != platform.python_version() or identity.get("platform") != sys.platform:
        raise ValueError("Installed Python runtime differs from attestation")
    if identity.get("dependencies") != packages():
        raise ValueError("Installed Python packages differ from attestation")
    files = identity.get("nativeFiles", {})
    if "native/forma-assembly" not in files or not any("libOndselSolver.so" in name for name in files):
        raise ValueError("Native binary attestation is incomplete")
    for name, expected in files.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or sha(path) != expected:
            raise ValueError("Installed native binary or notice differs from attestation")
    return identity


def runtime_version(root):
    root = Path(root)
    verify(root)
    return "forma-" + hashlib.sha256(b"".join((root / name).read_bytes() for name in FILES)).hexdigest()[:16]


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    if sys.argv[1:] == ["attest"]:
        attest(root)
    print(json.dumps({"runtimeVersion": runtime_version(root)}))
