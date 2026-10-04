from copy import deepcopy
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Manifest, Parameter, Requirement, Role, SafeId, SourcePath, safe_path


class Empty(Contract):
    pass


class ReadFile(Contract):
    # Empty paths were previously accepted by the model contract and only
    # rejected later by ``safe_path``. That let providers spend a full model
    # turn repeating ``read_file({path: ""})`` before the bounded retry guard
    # could stop the run.
    path: str = Field(min_length=1, max_length=180, pattern=r"^(?:parts|assemblies|calculations)/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.py$")


class Search(Contract):
    query: str = Field(min_length=1, max_length=200)


class ApplyChanges(Contract):
    files: dict[str, str]
    manifest: Manifest | None = None
    deletePaths: list[SourcePath] = Field(default_factory=list, max_length=200)

    @field_validator("files", mode="before")
    @classmethod
    def decode_file_entries(cls, value):
        if isinstance(value, list):
            return {item["path"]: item["content"] for item in value
                    if isinstance(item, dict) and "path" in item and "content" in item}
        return value


def updated_manifest(previous: dict, change: ApplyChanges) -> dict:
    """Preserve omitted top-level fields; explicit fields replace their values."""
    if change.manifest is None:
        return previous
    return {**previous, **change.manifest.model_dump(include=change.manifest.model_fields_set)}


class ParameterChange(Contract):
    componentId: SafeId
    parameter: str = Field(min_length=1, max_length=200)
    value: Parameter = Field(description=(
        "New value in the existing parameter's units and data type. Use numeric millimeters "
        "for CAD dimensions, not a string containing units."
    ))


class UpdateParameters(Contract):
    changes: list[ParameterChange] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_targets(self):
        targets = [(change.componentId, change.parameter) for change in self.changes]
        if len(set(targets)) != len(targets):
            raise ValueError("A parameter target cannot appear twice in one update.")
        return self


class ReviewResponse(Contract):
    findingId: SafeId
    explanation: str = Field(min_length=1, max_length=3000)
    evidence: list[str] = Field(min_length=1, max_length=20)


def parameter_patch(previous: dict, update: UpdateParameters) -> ApplyChanges:
    """Translate a small parameter delta into the existing atomic edit path."""
    components = deepcopy(previous["components"])
    definitions = {component["id"]: component for component in components}
    for change in update.changes:
        component = definitions.get(change.componentId)
        if component is None:
            raise ValueError("The parameter update names an unknown component.")
        parameters = component.get("parameters", {})
        if change.parameter not in parameters:
            raise ValueError("The parameter update names an unknown existing parameter. Use apply_changes to introduce new source parameters.")
        parameters[change.parameter] = change.value
    return ApplyChanges(files={}, manifest=Manifest.model_validate({"components": components}))


class Build(Contract):
    final: bool = Field(default=True, description=(
        "False for an intermediate assembly milestone; true only when all requested "
        "part types and instances are present and the draft is ready to publish."
    ))


class Delegate(Contract):
    role: Literal["cad", "engineering"]
    task: str = Field(min_length=1, max_length=6000)
    requirements: list[Requirement] = Field(default_factory=list, max_length=100)


class Publish(Contract):
    summary: str = Field(min_length=1, max_length=1000)


class Restore(Publish):
    revisionId: str


class Question(Contract):
    question: str = Field(min_length=1, max_length=3000)


class Finish(Contract):
    message: str = Field(min_length=1, max_length=8000)


class RequestEngineering(Contract):
    task: str = Field(min_length=1, max_length=6000)


SPECS = {
    "read_file": (ReadFile, "Read a private workspace source file before editing it."),
    "search_files": (Search, "Search private workspace files by literal text."),
    "apply_changes": (ApplyChanges, "Atomically stage related files and optional manifest changes. Omitted top-level manifest fields are preserved; provided fields and arrays replace their values. Include complete entries in provided arrays. Does not execute code."),
    "update_parameters": (UpdateParameters, "Atomically change existing named component parameters without rewriting source or assembly relationships. Read the component source first. New parameters require apply_changes. Rebuild and validate before publication."),
    "respond_to_review": (ReviewResponse, "Read-only response when a specific repair finding contradicts current validated evidence. Return the unchanged validated candidate to its reviewer for reassessment once per finding. Does not edit, validate or publish geometry."),
    "build": (Build, "Build and validate the current CAD workspace. Use final=false for an intermediate assembly milestone, or final=true only when the requested design is represented."),
    "inspect_geometry": (Empty, "Inspect the current candidate's build report and optional requirement evidence."),
    "inspect_project": (Empty, "Inspect the current project, previous conversation, selected parts, revision, and verification evidence."),
    "request_engineering": (RequestEngineering, "Ask the engineering agent for calculations or design parameters, then return to CAD."),
    "calculate": (ReadFile, "Execute a calculations/ module twice in separate Python processes."),
    "delegate": (Delegate, "Delegate a complete task and explicit requirements to one specialist."),
    "publish_revision": (Publish, "Publish the exact successfully built candidate as a draft for human review."),
    "restore_revision": (Restore, "Load an existing owned revision as the candidate; it must be rebuilt before publication."),
    "ask_user": (Question, "Pause and ask for missing information or explain an unsupported requirement."),
    "finish": (Finish, "Finish with a factual answer supported by completed tool results."),
}
ROLE_TOOLS = {
    "coordinator": ("inspect_project", "read_file", "search_files", "delegate", "inspect_geometry", "publish_revision", "restore_revision", "ask_user", "finish"),
    "cad": ("read_file", "search_files", "apply_changes", "update_parameters", "respond_to_review", "build", "inspect_geometry", "request_engineering", "ask_user", "finish"),
    "engineering": ("read_file", "search_files", "apply_changes", "calculate", "inspect_geometry", "ask_user", "finish"),
}


def portable_schema(schema: dict) -> dict:
    definitions = schema.get("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(v) for v in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand(definitions[value["$ref"].rsplit("/", 1)[-1]])
        # Function declarations are an OpenAPI subset.  Pydantic's JSON
        # Schema is deliberately richer (nullable ``anyOf`` unions,
        # ``additionalProperties`` from ``extra=forbid``, ``const`` defaults,
        # and Python-only object-key constraints).  Sending those keywords to
        # Gemini causes a provider-level 400 before the model sees the prompt.
        # Runtime Pydantic validation remains the authoritative contract after
        # the tool call, so these presentation-only restrictions can be
        # omitted safely from the model-facing declaration.
        # Keep only the fields accepted by Gemini's function-declaration
        # subset.  Bounds and regexes remain enforced by Pydantic after the
        # call; forwarding them is unnecessary and some Gemini versions reject
        # them as an invalid argument.
        # Keep the intermediate union/tuple keywords until after they have
        # been normalized below.  Filtering them before normalization turns
        # optional fields into `{}` and fixed tuples into untyped arrays;
        # Gemini can then silently return an empty tool call for otherwise
        # valid structured requests.
        allowed = {"type", "description", "enum", "items", "properties", "required",
                   "anyOf", "prefixItems"}
        result = {}
        for key, raw in value.items():
            if key not in allowed:
                continue
            # Property names are data, not schema keywords; preserve them
            # while sanitizing each nested property schema.
            if key == "properties" and isinstance(raw, dict):
                result[key] = {name: expand(child) for name, child in raw.items()}
            else:
                result[key] = expand(raw)
        if "anyOf" in result:
            branches = result.pop("anyOf")
            non_null = [branch for branch in branches
                        if not (isinstance(branch, dict) and branch.get("type") == "null")]
            # Optional Pydantic fields are represented as ``T | null``.  The
            # field itself is not required, so Gemini only needs the T branch.
            if len(non_null) == 1:
                nullable = expand(non_null[0])
                if isinstance(nullable, dict):
                    nullable.setdefault("description", result.get("description", ""))
                return nullable
            # Heterogeneous unions (for example manifest parameter values)
            # cannot be expressed in Gemini's function schema subset.  Leave
            # an unconstrained value and let the strict Pydantic contract
            # reject malformed arguments with a repairable validation error.
            result = {"description": result.get("description", "")}
        # Pydantic represents fixed-length tuples as ``prefixItems``. Google
        # Gemini's function declaration schema accepts only a homogeneous
        # ``items`` schema for arrays, even when minItems/maxItems retain the
        # tuple length. All Forma tuple fields are homogeneous numeric vectors.
        if "prefixItems" in result and "items" not in result:
            prefix = result.pop("prefixItems")
            if prefix:
                result["items"] = prefix[0]
        if result.get("type") == "array" and "items" not in result:
            result["items"] = {}
        # Gemini does not reliably generate arbitrary-key dictionaries. File
        # workspaces are advertised as a typed list while Pydantic preserves
        # the internal dict contract after the tool call.
        properties = result.get("properties")
        if isinstance(properties, dict):
            files_schema = properties.get("files")
            if (isinstance(files_schema, dict) and files_schema.get("type") == "object"
                    and not files_schema.get("properties")):
                properties["files"] = {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                        "required": ["path", "content"],
                    },
                }
        return result
    return expand(schema)


def model_tools(role: Role, *, review_response: bool = False) -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": SPECS[name][1],
            "parameters": portable_schema(SPECS[name][0].model_json_schema())}} for name in ROLE_TOOLS[role]
            if name != "respond_to_review" or review_response]


def parse_tool(role: Role, name: str, value: dict):
    if name not in ROLE_TOOLS[role]:
        raise ValueError("This role cannot use that tool.")
    parsed = SPECS[name][0].model_validate(value)
    if isinstance(parsed, ReadFile):
        safe_path(parsed.path)
    return parsed
