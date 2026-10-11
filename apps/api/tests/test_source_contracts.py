import json

import pytest

from forma_api.contracts import Snapshot
from forma_api.source_contracts import component_entrypoint_issues, validate_component_entrypoints
from forma_api.workspace_diagnostics import workspace_contract_feedback


def manifest():
    return {"components": [
        {"id": "cover", "name": "Cover", "kind": "solid", "source": "parts/cover.py"},
        {"id": "shaft", "name": "Shaft", "kind": "solid", "source": "parts/shaft.py"}],
        "rootComponentId": "cover"}


@pytest.mark.parametrize("signature", [
    "parameters", "parameters, dependencies, required", "parameters, *, dependencies", ""])
def test_rejects_known_incompatible_entrypoint_before_source_can_be_saved(signature):
    files = {"parts/cover.py": f"def build({signature}):\n    return 'PRIVATE_SOURCE'\n",
             "parts/shaft.py": "def build(parameters, dependencies):\n    return None\n"}
    with pytest.raises(ValueError) as error:
        validate_component_entrypoints(manifest(), files)
    feedback = workspace_contract_feedback(manifest(), files, error.value)
    assert [issue["path"] for issue in feedback["issues"]] == ["parts/cover.py"]
    assert feedback["issues"][0]["code"] == "build_signature"
    assert "PRIVATE_SOURCE" not in json.dumps(feedback)


@pytest.mark.parametrize("signature", [
    "parameters, dependencies", "params, deps=None", "a, b, /", "a, b, optional=1", "*args"])
def test_accepts_python_signatures_that_accept_two_positional_arguments(signature):
    files = {"parts/cover.py": f"def build({signature}):\n    return None\n",
             "parts/shaft.py": "def build(parameters, dependencies):\n    return None\n"}
    Snapshot.model_validate({"manifest": manifest(), "files": files})
    validate_component_entrypoints(manifest(), files)


def test_reports_all_component_signature_defects_without_executing_source():
    files = {"parts/cover.py": "raise RuntimeError('Must never execute')\ndef build(params):\n    pass\n",
             "parts/shaft.py": "async def build(parameters, dependencies):\n    pass\n",
             "parts/helper.py": "def build(single):\n    pass\n"}
    issues = component_entrypoint_issues(manifest(), files)
    assert {item["path"] for item in issues} == {"parts/cover.py", "parts/shaft.py"}
    assert issues[0]["line"] == 2


def test_does_not_claim_static_proof_for_imported_or_decorated_entrypoints():
    files = {"parts/cover.py": "from helper import build\n",
             "parts/shaft.py": "@wrapper\ndef build(parameters):\n    pass\n"}
    assert component_entrypoint_issues(manifest(), files) == []
