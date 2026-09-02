import pytest

from forma_api.contracts import ResumeRequest
from forma_api.graphs.design import phase_route, triage_route


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
