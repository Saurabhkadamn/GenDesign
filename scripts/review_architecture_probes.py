"""Offline review probes: characterize current behavior without changing production.

Run from apps/api with its Python environment. All database/provider calls below
are mocked. These assertions document defects/limits, not desired behavior.
"""
import asyncio
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps/api"))

from forma_api.contracts import Snapshot
from forma_api.graphs import design
from forma_api.repositories import projects
from forma_api.agent_memory import record_observation, read_memory


async def probes():
    findings = {}
    # Different fully stated inputs still select the fixed spacer fixture.
    request = ("Calculate tolerance and end-float for spacer A 10 mm, spacer B "
               "10 mm and spacer C 10 mm in an exact 40 mm gap.")
    namespace = {}
    exec(design.deterministic_tolerance_calculation_source(request), namespace)
    calculation = namespace["calculate"]()
    actual = calculation["results"]["nominal_end_float"]["value"]
    assert abs(actual - 0.1) < 1e-9 and abs(actual - 10.0) > 1
    findings["hardcoded_calculation"] = {
        "expected_nominal_mm": 10.0, "returned_nominal_mm": round(actual, 8),
        "request_gap_mm": 40.0,
        "used_gap_mm": calculation["inputs"]["gap_nominal"]["value"],
    }

    original = "Initial brief " + "x" * 12000 + " TAIL_REQUIREMENT_947"
    early = "EARLY_CORRECTION_947: preserve the 16 mm journal."
    messages = [{"run_id": "old", "role": "user", "content": early}]
    messages.extend({"run_id": "old", "role": "user", "content": f"Later note {i}"}
                    for i in range(15))

    async def owned(*_):
        return {"name": "Offline review"}

    async def rest(table, *, params):
        if table != "messages":
            return []
        if params.get("role") == "eq.user":
            return [{"run_id": "first", "role": "user", "content": original}]
        return list(reversed(messages))

    with patch.object(projects, "owned_project", owned), patch.object(projects.db, "rest", rest):
        context = await projects.agent_context("project", "owner", "new", None, [])
    serialized = json.dumps(context)
    assert "TAIL_REQUIREMENT_947" not in serialized
    assert "EARLY_CORRECTION_947" not in serialized
    findings["cross_run_context"] = {
        "original_length": len(original),
        "retained_original_length": len(context["authoritativeInputs"]["originalBrief"]),
        "retained_corrections": len(context["authoritativeInputs"]["humanCorrections"]),
        "tail_requirement_missing": True, "early_correction_missing": True,
    }

    snapshot = Snapshot().model_dump()
    captured = {}

    async def structured(state, role, node, prompt, tool_name, contract, **kwargs):
        captured["prompt"] = prompt
        return design.Analysis(summary="Offline probe, no calculation"), {"model_calls": 1}

    async def load(*_):
        return snapshot

    state = {"run_id": "offline", "original_request": "Check the measured shaft clearance.",
             "engineering_request": "Compare the current built shaft with its bore.",
             "validation": {"report": {"measured_diameter_mm": 15.973947}},
             "build_result": {"measurement_marker": "BUILT_MEASUREMENT_947"}}
    with patch.object(design, "structured_turn", structured), patch.object(design.run_service, "load_candidate", load):
        await design.engineering_analysis(state)
    assert "15.973947" not in captured["prompt"] and "BUILT_MEASUREMENT_947" not in captured["prompt"]
    findings["engineering_handoff"] = {
        "current_validation_measurement_in_analysis_packet": False,
        "current_build_report_in_analysis_packet": False,
        "note": "A CAD task string could manually copy facts; automatic evidence transfer is absent.",
    }

    review = {"summary": "Known mismatch", "action": "repair", "findings": [{
        "id": "wrong_bore", "statement": "Bore must be 16 mm", "status": "observed_mismatch",
        "severity": "error", "evidence": ["Measured 18 mm"],
        "explanation": "Wrong diameter", "repair_instruction": "Correct the bore"}]}

    async def event(*_, **__):
        pass

    with patch.object(design.repo, "event", event):
        result = await design.record_review_result(
            {"run_id": "offline", "review_repairs": 2}, review, snapshot, {}, [])
    assert result["phase"] == "publish"
    findings["review_budget"] = {
        "known_geometric_error_after_two_repairs": result["phase"],
        "finding_preserved": result["review"]["findings"][0]["id"] == "wrong_bore",
        "note": "Draft publication is intentional; this is not full task acceptance.",
    }

    state = {"working_memory": record_observation({}, "build", {"ok": True}, candidate="A")}
    memory = record_observation(state, "build", {"ok": True}, candidate="B")
    retained = read_memory(memory, 0, 40)["outcomes"]
    assert len(retained) == 1 and retained[0]["candidateHash"] == "B"
    findings["working_memory_is_not_event_history"] = {
        "two_successful_build_observations": 2,
        "retrievable_build_observations": len(retained),
        "note": "Operation records may retain full history; this agent tool exposes only deduplicated latest facts.",
    }

    findings["pump_spec_arithmetic"] = {
        "listed_instance_count": sum([1, 1, 1, 1, 1, 4, 1, 2, 6, 6, 2, 1]),
        "radial_mm": {"min": round((38.100 - 37.970) / 2, 6),
                      "max": round((38.125 - 37.950) / 2, 6), "passes": True},
        "axial_mm": {"min": round(20.050 - 19.990, 6),
                      "max": round(20.070 - 19.980, 6), "passes": False},
        "journal_diametral_mm": {"min": round(16.000 - 15.984, 6),
                                  "max": round(16.018 - 15.966, 6), "passes": True},
        "basis": "User-specified tolerances only; not measurements of a built model.",
    }
    return findings


if __name__ == "__main__":
    result = asyncio.run(probes())
    payload = {"reviewed_commit": "c9205e1", "kind": "offline_mocked_behavior_probes", "results": result}
    if len(sys.argv) > 1:
        Path(sys.argv[1]).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
