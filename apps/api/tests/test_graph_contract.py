import pytest

from forma_api.contracts import ResumeRequest
from forma_api.graphs.design import ReviewResult, phase_route, triage_route


@pytest.mark.parametrize("route", ["clarify", "analyze", "cad", "answer"])
def test_engineering_routes_are_fixed(route):
    assert triage_route({"route": route}) == route


@pytest.mark.parametrize("phase", ["cad_design", "repair", "publish", "final"])
def test_graph_phase_routes_are_explicit(phase):
    assert phase_route({"phase": phase}) == phase


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
