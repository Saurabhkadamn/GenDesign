"""Pydantic contracts shared by HTTP, agent tools and the isolated CAD runner."""
import json
import math
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Role = Literal["coordinator", "cad", "engineering"]
RunStatus = Literal["queued", "running", "waiting_input", "paused", "succeeded", "failed", "cancelled"]
TERMINAL = {"waiting_input", "paused", "succeeded", "failed", "cancelled"}
SafeId = Annotated[str, Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")]
Vector = tuple[float, float, float]
NumericList = Annotated[list[float], Field(max_length=1000)]
NumericMatrix = Annotated[list[NumericList], Field(max_length=1000)]
Parameter = float | str | bool | NumericList | NumericMatrix
SOURCE_PATH_PATTERN = r"^(?:parts|assemblies|calculations)/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.py$"
SourcePath = Annotated[str, Field(pattern=SOURCE_PATH_PATTERN, max_length=180)]
GEOMETRY_SOURCE_PATH_PATTERN = r"^(?:parts|assemblies)/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.py$"
GeometrySourcePath = Annotated[str, Field(pattern=GEOMETRY_SOURCE_PATH_PATTERN, max_length=180)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, json_schema_serialization_defaults_required=True)


def safe_path(path: str) -> str:
    if len(path) > 180 or not re.fullmatch(SOURCE_PATH_PATTERN, path):
        raise ValueError("Use a relative Python path inside parts/, assemblies/ or calculations/.")
    return path


class Frame(Contract):
    position: Vector
    rotation: Vector


class Component(Contract):
    id: SafeId
    name: str = Field(min_length=1, max_length=100)
    source: GeometrySourcePath
    kind: Literal["solid", "surface", "assembly"]
    dependencies: list[SafeId] = Field(default_factory=list, max_length=100)
    parameters: dict[str, Parameter] = Field(default_factory=dict, max_length=200)
    color: str = Field(default="#b9c4ad", pattern=r"^#[0-9a-fA-F]{6}$")

    @field_validator("source")
    @classmethod
    def geometry_path(cls, path: str) -> str:
        if not re.fullmatch(GEOMETRY_SOURCE_PATH_PATTERN, path):
            raise ValueError("Geometry components must use a Python source inside parts/ or assemblies/.")
        return path


class Instance(Contract):
    id: SafeId
    definitionId: SafeId
    parentId: SafeId | None
    name: str = Field(min_length=1, max_length=100)
    frame: Frame


class Manifest(Contract):
    schemaVersion: Literal[1] = 1
    units: Literal["mm"] = "mm"
    components: list[Component] = Field(default_factory=list, max_length=200)
    instances: list[Instance] = Field(default_factory=list, max_length=1000)
    rootComponentId: SafeId | None = None


class Snapshot(Contract):
    manifest: Manifest = Field(default_factory=Manifest)
    files: dict[SourcePath, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def graph(self):
        for path, content in self.files.items():
            safe_path(path)
            if len(content) > 150_000:
                raise ValueError("Source file exceeds 150 KB.")
        if len(self.model_dump_json().encode()) > 2_000_000:
            raise ValueError("Workspace source exceeds 2 MB.")
        definitions = {c.id: c for c in self.manifest.components}
        if len(definitions) != len(self.manifest.components):
            raise ValueError("Duplicate component ID.")
        visited, visiting = set(), set()

        def visit(cid):
            if cid in visiting:
                raise ValueError("Cyclic component dependencies.")
            if cid in visited:
                return
            if cid not in definitions:
                raise ValueError("Unknown component dependency.")
            item = definitions[cid]
            if item.source not in self.files:
                raise ValueError(f"Missing source: {item.source}")
            visiting.add(cid)
            for dependency in item.dependencies:
                visit(dependency)
            visiting.remove(cid)
            visited.add(cid)

        for cid in definitions:
            visit(cid)
        if self.manifest.rootComponentId and self.manifest.rootComponentId not in definitions:
            raise ValueError("Unknown root component.")
        instances = {i.id: i for i in self.manifest.instances}
        if len(instances) != len(self.manifest.instances):
            raise ValueError("Duplicate instance ID.")
        for item in instances.values():
            if item.definitionId not in definitions:
                raise ValueError("Unknown instance definition.")
            parent, chain = item.parentId, {item.id}
            while parent:
                if parent in chain or parent not in instances:
                    raise ValueError("Invalid assembly hierarchy.")
                chain.add(parent)
                parent = instances[parent].parentId
        return self


class Limits(Contract):
    maxModelCalls: int = Field(default=12, ge=1, le=30)
    maxRepairs: int = Field(default=2, ge=0, le=3)
    commandTimeoutSeconds: int = Field(default=180, ge=30, le=300)
    maxArtifactBytes: int = Field(default=41943040, ge=1024, le=41943040)
    retainedExports: int = Field(default=5, ge=1, le=10)
    monthlySandboxSeconds: int = Field(default=7200, ge=60, le=18000)
    storageBudgetBytes: int = Field(default=800000000, ge=1048576, le=900000000)


class AppSettings(Contract):
    emergencyStop: bool = False
    engineeringEnabled: bool = True
    surfacingEnabled: bool = True
    limits: Limits = Field(default_factory=Limits)


class ChatRequest(Contract):
    message: str = Field(min_length=1, max_length=12000)
    baseRevisionId: UUID | None
    selectedIds: list[SafeId] = Field(default_factory=list, max_length=100)
    idempotencyKey: UUID

    @field_validator("message")
    @classmethod
    def strip_message(cls, value):
        if not value.strip():
            raise ValueError("Enter a design request.")
        return value.strip()


class ResumeRequest(Contract):
    kind: Literal["answer", "approval", "rejection", "continue"]
    message: str | None = Field(default=None, max_length=12000)

    @model_validator(mode="after")
    def message_for_answer(self):
        if self.kind == "answer" and not (self.message or "").strip():
            raise ValueError("An answer is required to resume this run.")
        if self.message is not None:
            self.message = self.message.strip()
        return self


class Quantity(Contract):
    value: float
    unit: str


class CalculationCheck(Contract):
    name: str
    passed: bool
    detail: str


class CalculationResult(Contract):
    title: str = Field(min_length=1, max_length=160)
    inputs: dict[str, Quantity]
    assumptions: list[str] = Field(max_length=30)
    equations: list[str] = Field(max_length=30)
    results: dict[str, Quantity]
    checks: list[CalculationCheck]
    conclusion: str = Field(max_length=4000)


class Requirement(Contract):
    """Numeric expectations are coordinator-owned, never mutable by generated CAD code."""
    id: SafeId
    description: str = Field(min_length=1, max_length=500)
    kind: Literal["dimensions", "center", "solid_count", "through_holes", "corner_radius", "unverified"]
    componentId: SafeId | None = None
    axis: Literal["X", "Y", "Z"] = "Z"
    dimensions: Vector | None = None
    center: Vector | None = None
    count: int | None = Field(default=None, ge=0, le=1000)
    diameter: float | None = Field(default=None, gt=0)
    radius: float | None = Field(default=None, gt=0)
    positions: list[tuple[float, float]] = Field(default_factory=list, max_length=1000)
    tolerance: float = Field(default=0.02, gt=0, le=0.1)

    @model_validator(mode="after")
    def values_for_kind(self):
        required = {"dimensions": [self.dimensions], "center": [self.center],
                    "solid_count": [self.count], "through_holes": [self.diameter, self.count],
                    "corner_radius": [self.radius, self.count], "unverified": []}[self.kind]
        if any(x is None for x in required):
            raise ValueError("Requirement is missing expected values.")
        if self.kind == "through_holes" and len(self.positions) != self.count:
            raise ValueError("Provide the XY center of every expected through-hole.")
        return self


class Profile(BaseModel):
    id: str
    email: str
    display_name: str
    role: Literal["admin", "engineer"]
    active: bool
    must_change_password: bool


class SessionView(Contract):
    configured: bool
    profile: Profile | None


class RunEvent(BaseModel):
    id: int
    run_id: str
    kind: Literal["status", "tool", "validation", "error"]
    message: str
    created_at: str
    stage: str | None = None
    attempt: int | None = None
    elapsed_ms: float | None = None


class TraceStatus(Contract):
    configured: bool
    project: str
    content: Literal["full", "metadata"]
    available: bool
    provider: Literal["LangSmith"] = "LangSmith"


class Project(BaseModel):
    id: str
    owner_id: str
    name: str
    current_revision_id: str | None
    created_at: str
    updated_at: str


class RequirementCheck(BaseModel):
    id: str
    description: str
    kind: str
    status: Literal["passed", "failed", "unverified"]
    evidence: dict


class ValidationReport(BaseModel):
    identity: dict[str, str]
    requirements: list[RequirementCheck]
    allRequirementsVerified: bool


class Revision(BaseModel):
    id: str
    project_id: str
    run_id: str
    ordinal: int
    summary: str
    manifest: Manifest
    created_at: str
    restored_from: str | None
    validation: ValidationReport | None = None


class Run(BaseModel):
    id: str
    project_id: str
    owner_id: str
    base_revision_id: str | None
    status: RunStatus
    message: str
    selected_ids: list[str]
    error: str | None
    created_at: str
    updated_at: str
    workflow_id: str | None
    model_calls: int
    backend_version: int
    execution_environment: str


class ChatMessage(BaseModel):
    id: str
    project_id: str
    run_id: str
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class Artifact(BaseModel):
    id: str
    project_id: str
    revision_id: str
    component_id: str | None
    name: str
    kind: Literal["step", "glb", "plot"]
    bytes: int
    storage_path: str


class CalculationRecord(BaseModel):
    id: str
    project_id: str
    run_id: str
    revision_id: str | None
    result: CalculationResult
    stale: bool
    reproducible: bool
    created_at: str


class WorkspaceState(BaseModel):
    project: Project
    revisions: list[Revision]
    messages: list[ChatMessage]
    runs: list[Run]
    artifacts: list[Artifact]
    calculations: list[CalculationRecord]
    events: list[RunEvent]


class ModelOption(BaseModel):
    id: str
    name: str
    contextLength: int


class ModelOptions(BaseModel):
    freeOnly: bool
    syntheticNemotronTesting: bool
    models: list[ModelOption]


class ModelConfigView(BaseModel):
    role: Role
    model_id: str
    key_hint: str
    active: bool
    version: int
    tested_at: str | None


class FrontendContracts(BaseModel):
    workspace: WorkspaceState
    session: SessionView
    settings: AppSettings
    models: ModelOptions
    model_config_view: ModelConfigView
    snapshot: Snapshot
    request: ChatRequest
