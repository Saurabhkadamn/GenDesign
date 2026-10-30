"""Shared generated helpers must reload across isolated CAD operations."""

import importlib.util
from pathlib import Path
import sys


RUNTIME = Path(__file__).resolve().parents[3] / "runtimes" / "python" / "forma_runtime.py"
SPEC = importlib.util.spec_from_file_location("forma_runtime_workspace_test", RUNTIME)
assert SPEC is not None and SPEC.loader is not None
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)


def test_workspace_helper_import_reloads_after_edit(tmp_path):
    package = tmp_path / "calculations"
    package.mkdir()
    (package / "main.py").write_text(
        "from calculations.shared import value\n"
        "def calculate(): return {'value': value()}\n"
    )
    helper = package / "shared.py"
    helper.write_text("def value(): return 1\n")

    with runtime.workspace_imports(tmp_path):
        first = runtime.module_at(tmp_path, "calculations/main.py").calculate()
    assert first == {"value": 1}
    assert "calculations.shared" not in sys.modules

    helper.write_text("def value(): return 2\n")
    with runtime.workspace_imports(tmp_path):
        second = runtime.module_at(tmp_path, "calculations/main.py").calculate()
    assert second == {"value": 2}
    assert "calculations.shared" not in sys.modules
