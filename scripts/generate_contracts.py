"""Run with apps/api/.venv Python, then openapi-typescript generates browser types."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))
from forma_api.main import app
from forma_api.contracts import Limits, Manifest

(ROOT / "packages/core/openapi.json").write_text(json.dumps(app.openapi(), indent=2) + "\n")
(ROOT / "packages/core/src/defaults.ts").write_text(
    "// Generated from Python/Pydantic. Do not edit by hand.\n"
    "import type { ExecutionLimits, ProjectManifest } from './index';\n"
    f"export const defaultLimits: ExecutionLimits = {json.dumps(Limits().model_dump())};\n"
    f"export const emptyManifest: ProjectManifest = {json.dumps(Manifest().model_dump())};\n"
)
