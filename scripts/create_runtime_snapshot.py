"""Compatibility entry point for the qualified native/Python image builder."""
from pathlib import Path
import runpy

if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).with_name("qualify_runtime_snapshot.py")), run_name="__main__")
