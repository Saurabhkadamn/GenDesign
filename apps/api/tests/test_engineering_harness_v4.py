import pytest

from forma_api.engineering_math import IntervalCalculation, calculate_intervals
from forma_api.agent_context import engineering_context, input_budget
from forma_api.agent_memory import record_observation, read_memory
from forma_api.completion import assess_completion
from forma_api.contracts import Snapshot
from forma_api.execution import identity


def interval(n, lo=None, hi=None, unit="mm"):
    return {
        "nominal": n,
        "minimum": n if lo is None else lo,
        "maximum": n if hi is None else hi,
        "unit": unit,
        "sourceRef": "brief:specified-dimension",
    }


@pytest.mark.parametrize("gap,spacer,expected", [(40, 10, 10), (50, 12, 14), (12.5, 2.5, 5)])
def test_tolerance_inputs_never_use_fixture_constants(gap, spacer, expected):
    result = calculate_intervals(
        IntervalCalculation(
            formula="gap - (a+b+c)",
            inputs={
                "gap": interval(gap),
                "a": interval(spacer),
                "b": interval(spacer),
                "c": interval(spacer),
            },
            outputUnit="mm",
        )
    )
    assert result["nominal"] == result["minimum"] == result["maximum"] == expected


def test_mixed_units_and_worst_case_chain():
    result = calculate_intervals(
        IntervalCalculation(
            formula="housing - gear",
            inputs={
                "housing": interval(20.06, 20.05, 20.07),
                "gear": interval(0.02, 0.01998, 0.01999, "m"),
            },
            outputUnit="mm",
            requiredMinimum=0.04,
            requiredMaximum=0.08,
        )
    )
    assert result["minimum"] == 0.06
    assert result["maximum"] == 0.09
    assert result["status"] == "failed"
    assert result["nominal"] == 0.06


@pytest.mark.parametrize(
    "formula,inputs",
    [
        ("length+mass", {"length": interval(1), "mass": interval(1, unit="kg")}),
        ("a/b", {"a": interval(1), "b": interval(0, -1, 1)}),
        ('__import__("os")', {"a": interval(1)}),
    ],
)
def test_invalid_units_singular_intervals_and_code_are_rejected(formula, inputs):
    with pytest.raises(ValueError):
        calculate_intervals(IntervalCalculation(formula=formula, inputs=inputs, outputUnit="mm"))


def test_engineering_sees_current_measurement_and_manifest():
    snapshot = Snapshot().model_dump()
    context = engineering_context(
        {"candidate_hash": "new", "validation": {"measuredBore": 38.11}}, snapshot
    )
    assert context["validation"]["measuredBore"] == 38.11
    assert context["candidateHash"] == "new"
    assert context["manifest"] == snapshot["manifest"]


def test_multiple_build_candidates_remain_retrievable():
    state = {"working_memory": record_observation({}, "build", {"ok": True}, candidate="a")}
    state["working_memory"] = record_observation(state, "build", {"ok": True}, candidate="b")
    history = read_memory(state["working_memory"], 0, 10)
    assert [item["candidateHash"] for item in history["outcomes"]] == ["b", "a"]


def test_failed_requirement_is_incomplete_despite_publishable_draft():
    snapshot = Snapshot().model_dump()
    requirements = [{"id": "bore", "kind": "dimensions", "description": "Correct bore"}]
    plan = {
        "version": "plan1",
        "sourceIds": ["source1"],
        "requirements": [{"requirement": requirements[0]}],
        "deliverables": [
            {"id": "step", "name": "part.step", "kind": "step", "description": "Part STEP"}
        ],
    }
    state = {
        "requirements": requirements,
        "acceptance_plan": plan,
        "review": {"coveragePlanVersion": "plan1", "coverageComplete": True},
        "reviewed_candidate_hash": identity(snapshot, requirements)["candidate"],
        "validation": {
            "identity": identity(snapshot, requirements),
            "artifacts": [
                {
                    "kind": "step",
                    "name": "part.step",
                    "storagePath": "private/part.step",
                    "contentHash": "hash",
                }
            ],
            "report": {"requirements": [{"id": "bore", "status": "failed"}]},
        },
    }
    result = assess_completion(state, snapshot)
    assert result["taskOutcome"] == "incomplete"
    assert result["acceptanceState"] == "draft"
    state["validation"]["report"]["requirements"][0]["status"] = "passed"
    assert assess_completion(state, snapshot)["acceptanceState"] == "ready_for_review"
    state["validation"]["identity"] = {"candidateHash": "old"}
    assert assess_completion(state, snapshot)["outstanding"][0]["status"] == "stale"


def test_agent_completion_claim_is_not_artifact_or_coverage_evidence():
    snapshot = Snapshot().model_dump()
    state = {
        "deliverable_coverage_complete": True,
        "deliverables": [{"status": "verified"}],
        "validation": {"identity": identity(snapshot, []), "report": {}},
    }
    assert assess_completion(state, snapshot)["acceptanceState"] == "draft"


def test_model_context_reserves_output_and_reasoning(monkeypatch):
    monkeypatch.delenv("FORMA_AGENT_CONTEXT_TOKENS", raising=False)
    assert input_budget({"context_length": 32000, "reasoning_token_reserve": 4000}, 8000) == 18400
    with pytest.raises(ValueError):
        input_budget({"context_length": 4096}, 4096)


@pytest.mark.asyncio
async def test_old_correction_and_long_original_brief_survive_new_run(monkeypatch):
    from forma_api.repositories import projects

    first = "x" * 13000 + " REQUIREMENT_AT_END"
    messages = [
        {"id": str(i), "run_id": "old", "role": "user", "content": content}
        for i, content in enumerate([first, "EARLY_AUTHORITATIVE_CORRECTION", *["later note"] * 65])
    ]

    async def owned(*args):
        return {"name": "test"}

    async def rest(table, params):
        if table != "messages":
            return []
        if params.get("role") == "eq.user":
            offset = params.get("offset", 0)
            return messages[offset : offset + params["limit"]]
        return list(reversed(messages[-60:]))

    monkeypatch.setattr(projects, "owned_project", owned)
    monkeypatch.setattr(projects.db, "rest", rest)
    context = await projects.agent_context("project", "owner", "new", None, [])
    assert context["authoritativeInputs"]["originalBrief"].endswith("REQUIREMENT_AT_END")
    assert "EARLY_AUTHORITATIVE_CORRECTION" in context["authoritativeInputs"]["humanCorrections"]
