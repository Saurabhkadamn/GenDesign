from copy import deepcopy
from uuid import uuid4

import pytest

from forma_api import engine
from forma_api.contracts import AppSettings, Snapshot
from forma_api.execution import build_error, identity
from forma_api.models import completion_settings, select_config, free_tool_model, failure
from forma_api.requirements import design_work_requested, explicit_requirements


def checkpoint(role="cad"):
    return {"snapshot": Snapshot().model_dump(), "role": role, "history": {"cad": [], "coordinator": [], "engineering": []},
            "requirements": [], "repairs": 0, "attempts": 1, "sequence": 0, "pending": []}


@pytest.mark.asyncio
async def test_unbuilt_cad_cannot_finish_or_publish():
    cp = checkpoint()
    call = {"id": "1", "name": "finish", "input": {"message": "Done"}}
    with pytest.raises(ValueError, match="cannot finish"):
        await engine.execute_tool({}, cp, call, AppSettings(), "worker")
    cp["role"] = "coordinator"
    call.update(name="publish_revision", input={"summary": "Done"})
    with pytest.raises(ValueError, match="exact independently"):
        await engine.execute_tool({}, cp, call, AppSettings(), "worker")


@pytest.mark.asyncio
async def test_batch_change_is_atomic_and_engineering_is_restricted():
    cp = checkpoint()
    original = deepcopy(cp)
    call = {"id": "1", "name": "apply_changes", "input": {"files": {"../escape.py": "oops"}}}
    with pytest.raises(ValueError):
        await engine.execute_tool({}, cp, call, AppSettings(), "worker")
    assert cp == original
    cp["role"] = "engineering"
    call["input"] = {"files": {"parts/plate.py": "anything"}}
    with pytest.raises(ValueError, match="Engineering can edit only"):
        await engine.execute_tool({}, cp, call, AppSettings(), "worker")


@pytest.mark.asyncio
async def test_unchanged_failed_candidate_is_not_executed():
    cp = checkpoint()
    cp["lastFailedCandidate"] = identity(cp["snapshot"], [])
    with pytest.raises(engine.Pause, match="has not changed"):
        await engine.build_candidate({}, cp, AppSettings().limits, "key")


@pytest.mark.asyncio
async def test_unverified_assembly_evidence_does_not_block_built_draft():
    from forma_api.graphs.design import validate

    result = await validate({
        "build_result": {
            "ok": True,
            "requirements": [{
                "id": "pivot_axis",
                "kind": "unverified",
                "status": "unverified",
            }],
        }
    })
    assert result == {"phase": "publish"}


@pytest.mark.asyncio
async def test_repeated_error_stops_repairs(monkeypatch):
    async def event(*a, **kw): pass
    monkeypatch.setattr(engine.repo, "event", event)
    cp = checkpoint()
    error = build_error({"diagnostic": "TypeError: 'list' object is not callable"}, "build")
    await engine.reject_candidate({"id": "run"}, cp, {}, error, AppSettings().limits)
    assert "stopAfterTool" not in cp
    await engine.reject_candidate({"id": "run"}, cp, {}, error, AppSettings().limits)
    assert "same build failure" in cp["stopAfterTool"]
    assert error["category"] == "edge_selector"


@pytest.mark.asyncio
async def test_ambiguous_model_operation_is_never_repeated(monkeypatch):
    async def one(*a, **kw): return {"status": "started"}
    async def update(*a, **kw): return []
    monkeypatch.setattr(engine.db, "one", one)
    monkeypatch.setattr(engine.db, "update", update)
    called = False
    async def paid_callback():
        nonlocal called
        called = True
    with pytest.raises(engine.Pause, match="not repeated automatically"):
        await engine.operation({"id": "run"}, "model:1", "model", paid_callback)
    assert not called


@pytest.mark.asyncio
async def test_completed_operation_replays_result_without_reexecution(monkeypatch):
    async def one(*a, **kw): return {"status": "complete", "result": {"ok": True}}
    monkeypatch.setattr(engine.db, "one", one)
    async def unexpected(): raise AssertionError("must not execute")
    assert await engine.operation({"id": "run"}, "model:1", "model", unexpected) == {"ok": True}


def test_specialists_inherit_only_a_tested_active_default():
    default = {"role": "coordinator", "active": True, "tested_at": "now"}
    cad = {"role": "cad", "active": False, "tested_at": "now"}
    assert select_config([default, cad], "cad") == default
    assert select_config([default, cad], "cad", testing=True) == cad
    assert select_config([{**default, "tested_at": None}], "engineering") is None


def test_paid_or_uncertain_pricing_cannot_pass_free_only_policy():
    model = {"id": "test:free", "pricing": {"prompt": "0", "completion": "0"}, "supported_parameters": ["tools"]}
    assert free_tool_model(model)
    assert not free_tool_model({**model, "pricing": {"prompt": "0", "completion": "0.01"}})
    assert not free_tool_model({**model, "pricing": {"prompt": "nan", "completion": "0"}})
    assert not free_tool_model({**model, "pricing": {"prompt": "0"}})


def test_exact_plate_constraints_are_not_left_to_model_claims():
    values = explicit_requirements("Design an aluminum mounting plate, 80 × 50 × 6 mm, centered at the origin. Add four 6 mm diameter through-holes at X = ±30 mm and Y = ±15 mm. Round the four outer corners with a 3 mm radius.")
    assert {v["kind"] for v in values} == {"dimensions", "center", "solid_count", "through_holes", "corner_radius"}
    assert next(v for v in values if v["kind"] == "through_holes")["count"] == 4


def test_actionable_design_requests_cannot_be_finished_as_chat():
    assert design_work_requested("Design a motor mounting bracket with four holes")
    assert design_work_requested("Design an aluminum mounting plate, 80 x 50 x 6 mm")
    assert not design_work_requested("Hello, what can you do?")


def test_coordinate_parameters_are_supported_without_accepting_executable_objects():
    from forma_api.contracts import Component
    from forma_api.tools import model_tools
    component = {"id": "plate", "name": "Plate", "source": "parts/plate.py", "kind": "solid",
                 "parameters": {"hole_positions": [[-30, -15], [-30, 15], [30, -15], [30, 15]]}}
    assert Component.model_validate(component).parameters["hole_positions"][0] == [-30, -15]
    with pytest.raises(ValueError):
        Component.model_validate({**component, "parameters": {"bad": {"code": "execute"}}})
    with pytest.raises(ValueError):
        Component.model_validate({**component, "parameters": {"bad": [float("nan")]}})
    assert any(t["function"]["name"] == "apply_changes" for t in model_tools("cad"))


def test_generated_workspace_accepts_only_safe_python_source_paths():
    from forma_api.graphs.design import Candidate, submission_tool
    with pytest.raises(ValueError, match="parts|assemblies|calculations"):
        Candidate.model_validate({"files": {"README.md": "not executable source"},
            "manifest": {}, "summary": "invalid"})
    schema = submission_tool("submit_candidate", "candidate", Candidate)["function"]["parameters"]
    # Provider-facing schemas omit Python/JSON-Schema key constraints; the
    # Candidate Pydantic contract still enforces the safe source-path pattern
    # after the model returns its tool arguments.
    assert "propertyNames" not in schema["properties"]["files"]


def test_tool_schemas_use_gemini_compatible_homogeneous_arrays():
    from forma_api.tools import model_tools

    schemas = model_tools("engineering")
    encoded = str(schemas)
    assert "prefixItems" not in encoded
    assert "anyOf" not in encoded
    assert "additionalProperties" not in encoded
    triage = next(item for item in schemas if item["function"]["name"] == "apply_changes")
    files = triage["function"]["parameters"]["properties"]["files"]
    assert files["type"] == "array"
    assert files["items"]["required"] == ["path", "content"]


def test_manifest_rejects_calculation_file_as_geometry_component():
    from forma_api.graphs.design import Candidate
    candidate = {
        "files": {"calculations/load_basis.py": "result = 1"},
        "manifest": {
            "schemaVersion": 1,
            "units": "mm",
            "components": [{
                "id": "load_basis", "name": "Load basis",
                "source": "calculations/load_basis.py", "kind": "solid",
                "dependencies": [], "parameters": {}, "color": "#b9c4ad",
            }],
            "instances": [],
            "rootComponentId": "load_basis",
        },
        "summary": "Invalid calculation component",
    }
    with pytest.raises(ValueError, match="parts|assemblies"):
        Candidate.model_validate(candidate)


def test_triage_keeps_incomplete_geometry_requirements_unverified():
    from forma_api.graphs.design import TriageRequirement, normalize_triage_requirements

    result = normalize_triage_requirements([TriageRequirement(
        id="frame_bolts", kind="through_holes", count=4,
        positions=[[-60, -50], [-60, 50], [60, -50], [60, 50]],
        description="Four M10 frame bolt positions; clearance diameter is unspecified",
    )])
    assert result[0]["kind"] == "unverified"
    assert "clearance diameter" in result[0]["description"]


def test_triage_preserves_complete_geometry_requirements():
    from forma_api.graphs.design import TriageRequirement, normalize_triage_requirements

    result = normalize_triage_requirements([TriageRequirement(
        id="holes", kind="through_holes", count=4, diameter=9,
        positions=[[-40, -30], [-40, 30], [40, -30], [40, 30]],
        description="Four motor clearance holes",
    )])
    assert result[0]["kind"] == "through_holes"
    assert result[0]["diameter"] == 9


def test_triage_accepts_gemini_serialized_numeric_vectors():
    from forma_api.graphs.design import Triage

    value = Triage.model_validate({
        "route": "cad",
        "requirements": [{
            "id": "envelope", "kind": "dimensions",
            "dimensions": "[160, 120, 140]", "description": "Envelope",
        }],
    })
    assert value.requirements[0].dimensions == (160.0, 120.0, 140.0)


def test_portable_schema_expands_optional_and_tuple_fields_for_provider_tools():
    from forma_api.graphs.design import Triage
    from forma_api.tools import portable_schema

    schema = portable_schema(Triage.model_json_schema())
    requirement = schema["properties"]["requirements"]["items"]
    assert requirement["properties"]["dimensions"]["type"] == "array"
    assert requirement["properties"]["dimensions"]["items"] == {"type": "number"}
    assert requirement["properties"]["componentId"]["type"] == "string"
    position = requirement["properties"]["positions"]["items"]
    assert position["items"] == {"type": "number"}


def test_triage_clamps_model_tolerance_to_runtime_precision_floor():
    from forma_api.graphs.design import TriageRequirement, normalize_triage_requirements

    result = normalize_triage_requirements([TriageRequirement(
        id="plate", kind="dimensions", dimensions=[100, 60, 6], tolerance=0.001,
        description="Plate dimensions",
    )])
    assert result[0]["tolerance"] == 0.05


def test_complex_requests_keep_distinct_model_hole_patterns():
    from forma_api.requirements import merge_requirements

    supplied = [
        {"id": "frame", "kind": "through_holes", "axis": "Y", "count": 4,
         "diameter": 11, "positions": [[-60, -50], [-60, 50], [60, -50], [60, 50]],
         "description": "Frame pattern"},
        {"id": "motor", "kind": "through_holes", "axis": "Z", "count": 4,
         "diameter": 9, "positions": [[-40, -30], [-40, 30], [40, -30], [40, 30]],
         "description": "Motor pattern"},
    ]
    result = merge_requirements(
        "160 mm × 120 mm × 140 mm, four Ø11 mm frame holes at X=±60 mm and four Ø9 mm motor holes at X=±40 mm",
        supplied,
    )
    assert [(r["id"], r["axis"]) for r in result if r["kind"] == "through_holes"] == [("frame", "Y"), ("motor", "Z")]


def test_double_escaped_python_source_is_normalized_without_touching_valid_source():
    from forma_api.execution import normalize_python_source

    escaped = "import math\\n\\ndef calculate():\\n\\treturn {}"
    normalized = normalize_python_source(escaped)
    assert normalized == "import math\n\ndef calculate():\n\treturn {}"
    valid = "value = '\\n'\n"
    assert normalize_python_source(valid) == valid


def test_openrouter_uses_each_model_advertised_completion_limit():
    modern = {"supported_parameters": ["max_completion_tokens", "tools"],
        "top_provider": {"max_completion_tokens": 128000}}
    legacy = {"supported_parameters": ["max_tokens", "tools"],
        "top_provider": {"max_completion_tokens": 65536}}
    assert completion_settings(modern) == ("max_completion_tokens", 128000)
    assert completion_settings(legacy) == ("max_tokens", 65536)
    assert completion_settings(modern, 2048) == ("max_completion_tokens", 2048)


def test_cad_requirements_bind_to_generated_manifest_datum():
    from forma_api.graphs.design import bind_requirements_to_manifest

    requirements = [{"id": "motor_clearance_holes", "description": "Motor mounting holes",
                     "kind": "through_holes", "componentId": "motor_mount_face", "axis": "Z",
                     "count": 4, "diameter": 9,
                     "positions": [[-40, -30], [-40, 30], [40, -30], [40, 30]], "tolerance": 0.05}]
    manifest = {"rootComponentId": "motor_mount_assembly", "components": [
        {"id": "motor_mount_bracket", "name": "Motor bracket", "kind": "solid",
         "parameters": {"motor_pattern_center_y": 75}},
        {"id": "motor_mount_assembly", "name": "Assembly", "kind": "assembly", "parameters": {}}
    ]}
    bound = bind_requirements_to_manifest(requirements, manifest)
    assert bound[0]["componentId"] == "motor_mount_bracket"
    assert bound[0]["positions"] == [[-40.0, 45.0], [-40.0, 105.0], [40.0, 45.0], [40.0, 105.0]]


def test_assembly_requirements_bind_unscoped_part_dimensions_by_name():
    from forma_api.graphs.design import bind_requirements_to_manifest

    requirements = [
        {"id": "req_base_dim", "description": "Base shell outer envelope",
         "kind": "dimensions", "dimensions": [90, 60, 30]},
        {"id": "req_pcb_dim", "description": "PCB placeholder plate dimensions",
         "kind": "dimensions", "dimensions": [70, 45, 1.6]},
    ]
    manifest = {"rootComponentId": "enclosure_assembly", "components": [
        {"id": "base_shell", "name": "Base Shell", "kind": "solid"},
        {"id": "pcb", "name": "PCB Plate", "kind": "solid"},
        {"id": "enclosure_assembly", "name": "IoT Sensor Enclosure Assembly", "kind": "assembly"},
    ]}
    bound = bind_requirements_to_manifest(requirements, manifest)
    assert [item["componentId"] for item in bound] == ["base_shell", "pcb"]
