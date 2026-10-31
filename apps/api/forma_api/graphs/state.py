"""Typed state for the deployment graph."""
from typing import Any, Literal, TypedDict


class AgentState(TypedDict, total=False):
    harness_version: int
    research_mode: bool
    task_outcome: str
    completion: dict[str, Any]
    acceptance_plan: dict[str, Any]
    structured_recovery: dict[str, Any]
    deliverables: list[dict[str, Any]]
    deliverable_coverage_complete: bool
    engineering_history: list[dict[str, Any]]
    engineering_calculation: dict[str, Any] | None
    run_id: str
    project_id: str
    owner_id: str
    base_revision_id: str | None
    original_request: str
    drawing_request: dict[str, Any] | None
    clarified_request: str
    user_updates: list[dict[str, str]]
    working_memory: dict[str, Any]
    context_estimated_tokens: int
    selected_ids: list[str]
    project_context: dict[str, Any]
    coordinator_history: list[dict[str, Any]]
    coordinator_task: str
    coordinator_pending_call: dict[str, Any]
    coordinator_actions: int
    coordinator_inspected_context: str
    coordinator_contract_repair: dict[str, Any]
    engineering_from_coordinator: bool
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
    build_request_key: str
    build_process_id: str
    validation_report: dict[str, Any]
    artifact_cursor: int
    artifact_receipts: list[dict[str, Any]]
    cad_last_build_failure: dict[str, Any]
    cad_component_probe_failures: list[str]
    build_final: bool
    requested_part_types: int
    built_part_types: int
    last_milestone_hash: str
    validation: dict[str, Any]
    published_revision_id: str
    cad_history: list[dict[str, Any]]
    cad_edits_since_build: int
    cad_recovery_build_required: bool
    cad_invalid_tool_attempts: int
    cad_contract_repair: dict[str, Any]
    cad_patch_mismatch: dict[str, Any]
    cad_observation_progress: dict[str, Any]
    cad_pending_actions: list[dict[str, Any]]
    cad_queue_feedback: dict[str, Any]
    component_probes: dict[str, Any]
    recovery_counts: dict[str, int]
    recovery_plan_count: int
    recovery_plan: dict[str, Any]
    recovery_source_paths: list[str]
    rejected_files: dict[str, str]
    last_read_path: str | None
    review_history: list[dict[str, Any]]
    review_reads: int
    review_inspected: bool
    review_actions: int
    review_repairs: int
    review: dict[str, Any]
    review_fingerprint: str
    reviewed_candidate_hash: str
    review_response: dict[str, Any]
    review_responses: list[str]
    pending_cad_call: dict[str, Any]
    engineering_request: str
    engineering_request_fingerprint: str
    engineering_no_action_fingerprint: str
    engineering_candidate_hash: str
    engineering_request_count: int
    last_engineering_request_hash: str
    repairs: int
    attempts: int
    last_failure: str
    last_failed_candidate: dict[str, str]
    sandbox: str
    validator: str
    sandbox_ready: bool
    model_calls: int
    model_calls_this_cycle: int
    no_action_streak: int
    search_count: int
    started_ns: int
    resume: dict[str, Any]
