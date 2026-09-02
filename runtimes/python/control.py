"""Root-owned sandbox supervisor. No source is imported into this process."""
import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import pwd
import resource
import shutil
import signal
import stat
import subprocess
import sys
import re
import time

JOB = Path("/job")
CAD = pwd.getpwnam("cad")


def cad_processes():
    found = []
    for path in Path("/proc").glob("[0-9]*/status"):
        try:
            if int(next(line.split()[1] for line in path.read_text().splitlines() if line.startswith("Uid:"))) == CAD.pw_uid:
                found.append(int(path.parent.name))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def cleanup():
    for _ in range(20):
        active = cad_processes()
        if not active:
            return True
        for pid in active:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        time.sleep(0.05)
    return not cad_processes()


def prepare():
    if not cleanup():
        raise RuntimeError("Leftover child processes could not be terminated")
    JOB.mkdir(exist_ok=True, mode=0o755)
    for name in ("workspace", "output"):
        path = JOB / name
        # Only fixed absolute child paths; never follow links left by generated code.
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            # The supervisor deliberately lacks DAC_OVERRIDE. Reclaim writable directories
            # before removing child files; generated code runs with no capabilities.
            os.chown(path, 0, 0)
            path.chmod(0o700)
            for directory, children, _ in os.walk(path, followlinks=False):
                for child_name in children:
                    child_path = Path(directory) / child_name
                    if not child_path.is_symlink():
                        os.chown(child_path, 0, 0)
                        child_path.chmod(0o700)
            shutil.rmtree(path)
        path.mkdir(mode=0o755)
    os.chown(JOB / "output", CAD.pw_uid, CAD.pw_gid)
    for name in ("receipt.json", "diagnostic.log"):
        (JOB / name).unlink(missing_ok=True)


def demote():
    os.setgroups([])
    os.setgid(CAD.pw_gid)
    os.setuid(CAD.pw_uid)
    # Disallow acquiring privileges through setuid binaries, even in the hosted VM.
    if ctypes.CDLL(None).prctl(38, 1, 0, 0, 0) != 0:
        raise RuntimeError("Could not set no_new_privs")
    resource.setrlimit(resource.RLIMIT_NPROC, (96, 96))
    resource.setrlimit(resource.RLIMIT_FSIZE, (40 * 1024 * 1024, 40 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def execute(operation, timeout, calculation_path):
    identity = json.loads((JOB / "workspace/identity.json").read_text())
    runtime_files = ("uv.lock", "forma_runtime.py", "requirements_check.py", "control.py")
    actual_runtime = "forma-" + hashlib.sha256(b"".join((Path("/opt/forma") / name).read_bytes() for name in runtime_files)).hexdigest()[:16]
    if identity.get("runtime") != actual_runtime:
        raise RuntimeError("Installed runtime hash does not match the candidate identity")
    command = ["/opt/forma/.venv/bin/python", "-I", "/opt/forma/forma_runtime.py", operation,
               "--root", str(JOB / "workspace"), "--output", str(JOB / "output")]
    if calculation_path:
        command += ["--path", calculation_path]
    started = time.monotonic()
    timed_out = False
    with (JOB / "diagnostic.log").open("wb") as log:
        child = subprocess.Popen(command, cwd=JOB / "workspace", stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, start_new_session=True, preexec_fn=demote,
            env={"PATH": "/usr/bin:/bin", "HOME": str(JOB / "output"), "TMPDIR": str(JOB / "output"),
                 "MPLBACKEND": "Agg", "OPENBLAS_NUM_THREADS": "2", "OMP_NUM_THREADS": "2"})
        try:
            code = child.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            child.kill()
            child.wait(timeout=5)
            code = 124
    clean = cleanup()
    with (JOB / "diagnostic.log").open("rb") as log:
        log.seek(max(0, os.fstat(log.fileno()).st_size - 8000))
        diagnostic = log.read(8000).decode("utf-8", errors="replace")
    receipt = {"identity": identity, "exitCode": code, "timedOut": timed_out,
               "clean": clean, "elapsedMs": (time.monotonic() - started) * 1000,
               "diagnostic": diagnostic if code else ""}
    (JOB / "receipt.json").write_text(json.dumps(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "execute", "cancel", "inspect", "read"])
    parser.add_argument("--operation", choices=["build", "validate", "calculate"], default="build")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--path", default="")
    args = parser.parse_args()
    if args.action == "read":
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*\.(step|glb|json)", args.path):
            raise ValueError("Invalid output filename")
        path = JOB / "output" / args.path
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= 40 * 1024 * 1024:
            raise ValueError("Invalid output file")
        sys.stdout.buffer.write(path.read_bytes())
        return
    if args.action == "prepare":
        prepare()
        result = {"ready": True}
    elif args.action == "cancel":
        result = {"clean": cleanup()}
    elif args.action == "inspect":
        result = json.loads((JOB / "receipt.json").read_text())
    else:
        result = execute(args.operation, min(300, max(1, args.timeout)), args.path)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
