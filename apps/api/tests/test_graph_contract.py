import pytest

from forma_api.contracts import ResumeRequest
from forma_api.graphs.design import ReviewResult, build_graph, checkpoint_view, phase_route, triage_route
from forma_api.requirements import explicit_requirements, merge_requirements


@pytest.mark.parametrize("route", ["clarify", "analyze", "cad", "answer"])
def test_engineering_routes_are_fixed(route):
    assert triage_route({"route": route}) == route


@pytest.mark.parametrize("phase", ["cad_design", "repair", "publish", "final"])
def test_graph_phase_routes_are_explicit(phase):
    assert phase_route({"phase": phase}) == phase


def test_successful_validation_routes_through_independent_review():
    edges = build_graph(None).get_graph().edges
    assert any(edge.source == "validate" and edge.target == "review_session" for edge in edges)
    assert not any(edge.source == "validate" and edge.target == "publish" for edge in edges)


def test_resume_contract_requires_an_answer_message():
    with pytest.raises(ValueError):
        ResumeRequest(kind="answer")
    assert ResumeRequest(kind="approval").message is None
    assert ResumeRequest(kind="answer", message="  12 mm  ").message == "12 mm"


def test_reviewer_contract_keeps_evidence_and_repair_instruction():
    result = ReviewResult.model_validate({
        "summary": "The output gear intersects the housing.",
        "action": "repair",
        "findings": [{
            "id": "output_gear_interference",
            "statement": "Output gear must clear the housing",
            "status": "observed_mismatch",
            "severity": "error",
            "evidence": ["intersection volume 51436 mm^3"],
            "explanation": "The gear occupies housing material.",
            "repair_instruction": "Cut a gear cavity before rebuilding.",
        }],
    })
    assert result.action == "repair"
    assert result.findings[0].status == "observed_mismatch"


def test_assembly_envelope_is_an_upper_bound_without_a_single_plate_check():
    request = """Design a scissor assembly with a base/mounting plate.
Components:
1. Base plate — fixed inside the door.
2. Guide rail — two instances.
Overall envelope (fits inside a door cavity): 400 x 300 x 60 mm.
"""
    values = explicit_requirements(request)
    assert [(item["id"], item["kind"]) for item in values] == [
        ("request_dimensions", "max_dimensions")]
    assert values[0]["dimensions"] == (400.0, 300.0, 60.0)
    supplied = [{"id": "model_size", "kind": "dimensions",
        "dimensions": [400.0, 300.0, 60.0]}]
    assert merge_requirements(request, supplied) == values
    legacy = [{"id": "request_dimensions", "kind": "dimensions",
        "dimensions": [400.0, 300.0, 60.0]},
        {"id": "request_solid", "kind": "solid_count", "count": 1}]
    assert checkpoint_view({"original_request": request, "requirements": legacy}, {})[
        "requirements"] == values


def test_exact_single_plate_still_requires_exact_dimensions_and_one_solid():
    values = explicit_requirements(
        "Design an aluminum mounting plate, 80 × 50 × 6 mm, centered at the origin.")
    assert {(item["id"], item["kind"]) for item in values} >= {
        ("request_dimensions", "dimensions"), ("request_solid", "solid_count")}
