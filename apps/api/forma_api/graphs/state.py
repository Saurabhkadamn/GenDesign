"""Typed state for the deployment graph."""
from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    run_id: str
    project_id: str
    owner_id: str
    base_revision_id: str | None
    original_request: str
    clarified_request: str
    selected_ids: list[str]
    phase: str
    route: Literal["clarify", "analyze", "cad", "answer"]
    question: str
    final_message: str
    terminal_status: Literal["succeeded", "failed"]
    engineering_remarks: list[str]
    engineering_assumptions: list[str]
    engineering_summary: str
    approval_summary: str
    approved: bool
    requirements: list[dict[str, Any]]
    candidate_hash: str
    candidate_summary: str
    build_result: dict[str, Any]
    validation: dict[str, Any]
    published_revision_id: str
    repairs: int
    attempts: int
    last_failure: str
    last_failed_candidate: dict[str, str]
    sandbox: str
    validator: str
    sandbox_ready: bool
    model_calls: int
    search_count: int
    started_ns: int
    resume: dict[str, Any]
