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
async def test_unverified_assembly_evidence_routes_to_focused_review():
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
    assert result["phase"] == "review_session"
    assert result["review_history"] == []
    assert result["review_reads"] == 0


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
async def test_explicit_continue_can_retry_an_ambiguous_model_operation(monkeypatch):
    async def one(*a, **kw):
        return {"status": "failed", "result": {"category": "user_retry_authorized"}}
    updates = []
    async def update(*a, **kw):
        updates.append((a, kw))
        return []
    monkeypatch.setattr(engine.db, "one", one)
    monkeypatch.setattr(engine.db, "update", update)
    called = False
    async def callback():
        nonlocal called
        called = True
        return {"ok": True}
    result = await engine.operation({"id": "run"}, "model:1", "model", callback)
    assert result == {"ok": True}
    assert called
    assert len(updates) == 2


@pytest.mark.asyncio
async def test_explicit_continue_authorizes_failed_model_http_retry_only(monkeypatch):
    calls = []
    updates = []
    async def rest(*args, **kwargs):
        calls.append((args, kwargs))
        return [{"operation_key": "model:1", "result": {"category": "timeout"}}]
    async def update(*args, **kwargs):
        updates.append((args, kwargs))
        return []
    monkeypatch.setattr(engine.db, "rest", rest)
    monkeypatch.setattr(engine.db, "update", update)
    await engine.authorize_ambiguous_retry("run")
    _, kwargs = calls[0]
    assert kwargs["params"]["kind"] == "in.(model,calculate,drawing_build)"
    assert kwargs["params"]["status"] == "in.(started,ambiguous,failed)"
    assert updates[0][0][1]["result"]["category"] == "user_retry_authorized"
    assert updates[0][0][1]["result"]["previous_category"] == "timeout"
    assert updates[0][1]["operation_key"] == "model:1"


@pytest.mark.asyncio
async def test_explicit_continue_can_retry_an_ambiguous_calculation(monkeypatch):
    async def one(*a, **kw):
        return {"status": "failed", "result": {"category": "user_retry_authorized"}}
    monkeypatch.setattr(engine.db, "one", one)
    async def update(*a, **kw):
        return []
    monkeypatch.setattr(engine.db, "update", update)
    called = False
    async def callback():
        nonlocal called
        called = True
        return {"ok": True}
    assert await engine.operation({"id": "run"}, "graph:engineering-calculation:x", "calculate", callback) == {"ok": True}
    assert called


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


def test_manifest_accepts_extensible_semantic_references_joints_and_configurations():
    snapshot = Snapshot.model_validate({
        "files": {"parts/arm.py": "def build(p,d): return None"},
        "manifest": {
            "components": [{"id": "arm", "name": "Arm", "source": "parts/arm.py",
                "kind": "solid", "material": {"name": "Aluminium", "densityKgM3": 2700}}],
            "instances": [{"id": "arm_1", "definitionId": "arm", "parentId": None,
                "name": "Arm", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}}],
            "rootComponentId": "arm",
            "references": [
                {"id": "pivot_axis", "componentId": "arm", "kind": "axis",
                 "origin": [0, 0, 0], "direction": [1, 0, 0]},
                {"id": "bracket_axis", "componentId": "arm", "kind": "custom_axis",
                 "origin": [0, 0, 0], "direction": [1, 0, 0]},
            ],
            "joints": [{"id": "pivot", "kind": "revolute", "referenceA": "pivot_axis",
                "referenceB": "bracket_axis", "lowerLimit": 0, "upperLimit": 25, "unit": "deg"}],
            "configurations": [{"id": "full_travel", "name": "Full travel", "frames": [{
                "instanceId": "arm_1", "frame": {"position": [0, 0, 0], "rotation": [25, 0, 0]}}]}],
            "featureOperations": [{"id": "pivot_bore", "componentId": "arm",
                "operation": "checked_cut", "description": "Cut the pivot bore"}],
        },
    })
    assert snapshot.manifest.joints[0].kind == "revolute"
    assert snapshot.manifest.configurations[0].id == "full_travel"


def test_manifest_rejects_joint_with_unknown_semantic_reference():
    with pytest.raises(ValueError, match="Joint references"):
        Snapshot.model_validate({
            "files": {"parts/arm.py": "def build(p,d): return None"},
            "manifest": {
                "components": [{"id": "arm", "name": "Arm", "source": "parts/arm.py", "kind": "solid"}],
                "instances": [], "rootComponentId": "arm", "references": [],
                "joints": [{"id": "pivot", "kind": "revolute", "referenceA": "missing_a",
                    "referenceB": "missing_b"}],
            },
        })


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


def test_cad_flattens_invalid_model_parent_edges_without_losing_instances():
    from forma_api.graphs.design import normalize_instance_hierarchy

    manifest = {"instances": [
        {"id": "base", "definitionId": "base_part", "parentId": "assembly",
         "name": "Base", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
        {"id": "lid", "definitionId": "lid_part", "parentId": "base",
         "name": "Lid", "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]}},
    ]}
    normalized, changed = normalize_instance_hierarchy(manifest)
    assert changed is True
    assert [item["id"] for item in normalized["instances"]] == ["base", "lid"]
    assert normalized["instances"][0]["parentId"] is None
    assert normalized["instances"][1]["parentId"] == "base"


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
    assert completion_settings(modern) == ("max_completion_tokens", 24576)
    assert completion_settings(legacy) == ("max_tokens", 24576)
    assert completion_settings(modern, 2048) == ("max_completion_tokens", 2048)


def test_completion_settings_respect_configured_budget(monkeypatch):
    monkeypatch.setenv("OPENROUTER_MAX_OUTPUT_TOKENS", "50000")
    modern = {"supported_parameters": ["max_completion_tokens", "tools"],
        "top_provider": {"max_completion_tokens": 128000}}
    limited = {"supported_parameters": ["max_tokens", "tools"],
        "top_provider": {"max_completion_tokens": 24000}}
    assert completion_settings(modern) == ("max_completion_tokens", 50000)
    assert completion_settings(limited) == ("max_tokens", 24000)


def test_muse_uses_its_supported_auto_tool_choice_without_weakening_other_models():
    from forma_api.providers.openrouter import tool_choice_setting
    tools = [{"type": "function"}]
    assert tool_choice_setting("meta/muse-spark-1.2-contributor", tools) == "auto"
    assert tool_choice_setting("z-ai/glm-5.3-flash", tools) == "required"
    assert tool_choice_setting("meta/muse-spark-1.2-contributor", []) == "none"


def test_bounded_history_never_sends_orphaned_tool_results():
    from forma_api.graphs.design import bounded_history
    history = [
        {"role": "user", "content": "design"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "old", "function": {}}]},
        {"role": "tool", "tool_call_id": "old", "content": "{}"},
        {"role": "assistant", "content": "", "tool_calls": [{"id": "kept", "function": {}}]},
        {"role": "tool", "tool_call_id": "kept", "content": "{}"},
    ]
    compacted = bounded_history(history, messages=2)
    assert all(item.get("tool_call_id") != "kept" for item in compacted)
    assert compacted == [{"role": "user", "content": "design"}]


def test_bounded_history_keeps_complete_tool_pairs():
    from forma_api.graphs.design import bounded_history
    pair = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "call", "function": {}}]},
        {"role": "tool", "tool_call_id": "call", "content": "{}"},
    ]
    assert bounded_history([{"role": "user", "content": "design"}, *pair]) == [
        {"role": "user", "content": "design"}, *pair]


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

from forma_api.execution import ExecutionFailure, SandboxExpired, build_error, identity


@pytest.mark.asyncio
async def test_expired_checkpoint_sandbox_gets_new_name_and_is_reused(monkeypatch):
    from forma_api import maintenance

    run = {"id": str(uuid4())}
    old = "forma-old-cad-deadbeef"
    cp = {"sandbox": old, "sandboxReady": True}
    created, probed, released, ledger = [], [], [], []

    class FakeExecutor:
        async def is_running(self, name):
            probed.append(name)
            if name == old:
                raise SandboxExpired("stopped")

        async def inspect(self, name):
            raise AssertionError("a fresh sandbox has no receipt to inspect")

        async def create(self, name):
            created.append(name)
            return name

    async def fake_operation(_run, key, kind, callback, *, idempotent=False):
        ledger.append((key, kind, idempotent))
        return await callback()

    async def release(name):
        released.append(name)

    async def ignore(*args, **kwargs):
        return None

    monkeypatch.setattr(engine, "executor", lambda: FakeExecutor())
    monkeypatch.setattr(engine, "operation", fake_operation)
    monkeypatch.setattr(engine, "settings", lambda: type("Settings", (), {
        "executor": "vercel", "resource_budgets_enabled": False})())
    monkeypatch.setattr(maintenance, "release_sandbox", release)
    monkeypatch.setattr(engine.tracing, "record", ignore)
    monkeypatch.setattr(engine.repo, "event", ignore)

    await engine.ensure_sandbox(run, cp, AppSettings().limits)
    replacement = cp["sandbox"]
    assert replacement != old
    assert replacement.startswith(f"forma-{run['id'].replace('-', '')}-cad-")
    assert cp["sandboxReady"] is True
    assert released == [old]
    assert created == [replacement]
    assert ledger == [(f"sandbox:{replacement}", "sandbox_prepare", True)]
    await engine.ensure_sandbox(run, cp, AppSettings().limits)
    assert created == [replacement]
    assert probed == [old, replacement, replacement]


@pytest.mark.asyncio
async def test_sandbox_probe_error_does_not_trigger_replacement(monkeypatch):
    cp = {"sandbox": "forma-live", "sandboxReady": True}

    class FakeExecutor:
        async def is_running(self, name):
            raise ExecutionFailure("inspection control failed")

        async def create(self, name):
            raise AssertionError("must not create a replacement")

    monkeypatch.setattr(engine, "executor", lambda: FakeExecutor())
    with pytest.raises(ExecutionFailure, match="inspection control failed"):
        await engine.ensure_sandbox({"id": str(uuid4())}, cp, AppSettings().limits)
    assert cp == {"sandbox": "forma-live", "sandboxReady": True}


@pytest.mark.asyncio
async def test_vercel_probe_checks_session_without_reading_build_receipt(monkeypatch):
    from types import SimpleNamespace
    from vercel import sandbox
    from forma_api.execution import VercelExecutor

    async def get_sandbox(*, name):
        return SimpleNamespace(current_session=SimpleNamespace(status=sandbox.SandboxStatus.RUNNING))

    async def unexpected_command(*args, **kwargs):
        raise AssertionError("probe must not invoke control.py inspect")

    monkeypatch.setattr(sandbox, "get_sandbox", get_sandbox)
    monkeypatch.setattr(VercelExecutor, "command", unexpected_command)
    assert await VercelExecutor().is_running("forma-fresh") is True


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expired", [(404, True), (503, False)])
async def test_vercel_missing_sandbox_is_expired_but_provider_outage_is_not(monkeypatch, status, expired):
    import httpx
    from vercel import sandbox
    from forma_api.execution import VercelExecutor

    async def get_sandbox(*, name):
        response = httpx.Response(status, request=httpx.Request("GET", f"https://example.test/{name}"))
        raise sandbox.SandboxApiError(response, "provider lookup failed")

    monkeypatch.setattr(sandbox, "get_sandbox", get_sandbox)
    expected = SandboxExpired if expired else sandbox.SandboxApiError
    with pytest.raises(expected):
        await VercelExecutor().box("forma-missing")


@pytest.mark.asyncio
async def test_unverified_assembly_evidence_preserves_focused_review():
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
    assert result["phase"] == "review_session"
    assert result["review"] == {}


@pytest.mark.asyncio
async def test_failed_build_retains_resumable_candidate_at_repair_limit(monkeypatch):
    import forma_api.graphs.design as design

    class Limits:
        maxRepairs = 3

    class Settings:
        limits = Limits()

    async def settings():
        return Settings()

    monkeypatch.setattr(design, "app_settings", settings)
    result = await design.validate({
        "repairs": 3,
        "attempts": 3,
        "cad_history": [],
        "build_result": {
            "ok": False,
            "error": {"guidance": "Inspect the failing operation and repair the candidate before rebuilding."},
        },
    })
    assert result["phase"] == "cad_recovery"
    assert "terminal_status" not in result
    assert "bounded CAD repair limit" in result["final_message"]
    assert "candidate is retained" in result["final_message"]


def test_assembly_root_mismatch_has_actionable_repair_guidance():
    error = build_error({"diagnostic":
        "ValueError: Assembly placements in manifest do not match the root STEP geometry"}, "validation")
    assert error["category"] == "assembly_root_mismatch"
    assert "CadQuery Assembly" in error["guidance"]
    assert "rootComponentId" in error["guidance"]


def test_openrouter_recovers_only_one_missing_outer_tool_list_bracket():
    from forma_api.providers.openrouter import _recover_text_tool_call

    tools = [{"type": "function", "function": {"name": "build"}}]
    recovered = _recover_text_tool_call('[[{"name":"build","parameters":{}}]', tools)
    assert recovered["name"] == "build"
    assert recovered["input"] == {}
    multiline = _recover_text_tool_call('[\n[\n{"name":"build","parameters":{}}\n]', tools)
    assert multiline["name"] == "build"
    assert _recover_text_tool_call('[[{"name":"build","parameters":{', tools) is None


def test_read_file_rejects_empty_and_outside_workspace_paths_at_contract_boundary():
    from pydantic import ValidationError
    from forma_api.tools import parse_tool

    with pytest.raises(ValidationError):
        parse_tool("cad", "read_file", {"path": ""})
    with pytest.raises(ValidationError):
        parse_tool("cad", "read_file", {"path": "README.md"})


def test_cad_unwraps_provider_item_wrappers_before_hierarchy_validation():
    from forma_api.graphs.design import normalize_instance_hierarchy

    manifest = {"instances": {"item": [
        {"id": "base", "definitionId": "base_part", "parentId": None,
         "name": "Base", "frame": {"position": {"item": [0, 0, 0]},
                                      "rotation": {"item": [0, 0, 0]}}},
    ]}}
    normalized, changed = normalize_instance_hierarchy(manifest)
    assert changed is False
    assert normalized["instances"][0]["id"] == "base"
    assert normalized["instances"][0]["frame"]["position"] == [0, 0, 0]
