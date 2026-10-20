from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Manifest, Parameter, Requirement, Role, SafeId, SourcePath, safe_path


class SourcePatch(Contract):
    path: SourcePath
    oldText: str = Field(min_length=1, description="Exact text occurring once in the saved file. Read the file first.")
    newText: str


class Empty(Contract):
    pass


class InspectGeometry(Contract):
    componentId: SafeId | None = Field(default=None, description=(
        "Optional CAD part-probe ID to retrieve its test report and source freshness. Omit for current full-candidate evidence."
    ))


class ReadFile(Contract):
    # Empty paths were previously accepted by the model contract and only
    # rejected later by ``safe_path``. That let providers spend a full model
    # turn repeating ``read_file({path: ""})`` before the bounded retry guard
    # could stop the run.
    path: str = Field(min_length=1, max_length=180, pattern=r"^(?:parts|assemblies|calculations)/(?:[a-zA-Z0-9_-]+/)*[a-zA-Z0-9_-]+\.py$")


class ReadSource(ReadFile):
    version: Literal["saved", "rejected"] = Field(default="saved", description=(
        "saved reads the authoritative workspace. rejected reads retained source from an unsaved rejected CAD edit; "
        "available to CAD only. It is a proposal only and has never been executed or verified."
    ))


class Search(Contract):
    query: str = Field(min_length=1, max_length=200)


class ApplyChanges(Contract):
    files: dict[str, str] = Field(default_factory=dict)
    manifest: Manifest | None = None
    deletePaths: list[SourcePath] = Field(default_factory=list, max_length=200)
    mergeById: bool = Field(default=False, description=(
        "True to upsert only supplied entries in manifest arrays by id, preserving other entries. "
        "Each supplied entry must be complete. False replaces supplied arrays. Omitted fields always remain unchanged."
    ))
    patches: list[SourcePatch] = Field(default_factory=list, max_length=100)
    dryRun: bool = Field(default=False, description="Validate the complete proposed edit without saving or executing it.")

    @field_validator("files", mode="before")
    @classmethod
    def decode_file_entries(cls, value):
        if isinstance(value, list):
            if any(not isinstance(item, dict) or set(item) != {"path", "content"}
                   or not isinstance(item.get("path"), str) or not isinstance(item.get("content"), str) for item in value):
                raise ValueError("Each file entry needs exactly path and content.")
            if len({item["path"] for item in value}) != len(value):
                raise ValueError("Duplicate file paths are not allowed.")
            return {item["path"]: item["content"] for item in value}
        return value


def updated_manifest(previous: dict, change: ApplyChanges) -> dict:
    """Preserve omitted top-level fields; explicit fields replace their values."""
    if change.manifest is None:
        return previous
    updates = change.manifest.model_dump(include=change.manifest.model_fields_set)
    if change.mergeById:
        for key, entries in list(updates.items()):
            if not isinstance(entries, list):
                continue
            ids = [item["id"] for item in entries]
            if len(set(ids)) != len(ids):
                raise ValueError(f"Duplicate IDs in manifest.{key} upsert.")
            merged = {item["id"]: deepcopy(item) for item in previous.get(key, [])}
            merged.update({item["id"]: item for item in entries})
            updates[key] = list(merged.values())
    return {**deepcopy(previous), **updates}


def source_patch(previous: dict[str, str], change: ApplyChanges) -> ApplyChanges:
    """Resolve exact text patches before the existing atomic workspace validation."""
    files = dict(change.files)
    for patch in change.patches:
        if patch.path in change.files or patch.path in change.deletePaths:
            raise ValueError("A patched path cannot also be replaced or deleted in the same action.")
        content = files.get(patch.path, previous.get(patch.path))
        if content is None:
            raise ValueError(f"No saved source exists at {patch.path}. Rejected proposals are not saved files; "
                             "read version='rejected' if available, then submit full source and its manifest entry.")
        if content.count(patch.oldText) != 1:
            raise ValueError(f"Patch for {patch.path} must match exactly once; read current source and retry.")
        files[patch.path] = content.replace(patch.oldText, patch.newText, 1)
    return change.model_copy(update={"files": files})


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


class ComponentTest(Contract):
    componentId: SafeId


class FullBuild(Contract):
    final: bool = Field(default=True, description="False for an assembly milestone; true to build the complete design for review and publication. No componentId: the saved root defines this scope.")


class Build(Contract):
    componentId: SafeId | None = Field(default=None, description=(
        "Optional isolated part/surface diagnostic: build this definition and its dependency closure without "
        "changing the saved assembly or publishing. Probe evidence is scoped to this component and source version."
    ))
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


NoteText = Annotated[str, Field(max_length=1000)]


class WorkItem(Contract):
    id: SafeId
    task: str = Field(min_length=1, max_length=1000)
    status: Literal["pending", "in_progress", "blocked", "done"] = "pending"
    dependsOn: list[SafeId] = Field(default_factory=list, max_length=100)
    evidence: str = Field(default="", max_length=1000, description="Source/test references and remaining uncertainty. Agent-reported; not a verification result.")


class WorkingNotes(Contract):
    objective: str = Field(default="", max_length=3000)
    currentTask: str = Field(default="", max_length=3000)
    decisions: list[NoteText] = Field(default_factory=list, max_length=40)
    pendingRequirements: list[NoteText] = Field(default_factory=list, max_length=100)
    failedApproaches: list[NoteText] = Field(default_factory=list, max_length=40)
    nextAction: str = Field(default="", max_length=3000)
    tasks: list[WorkItem] = Field(default_factory=list, max_length=100, description=(
        "Editable work breakdown and acceptance tasks. Preserve every requested deliverable, including unsupported "
        "checks as blocked/unverified notes. A done item does not certify geometry or replace trusted validation."
    ))


class ReadWorkingMemory(Contract):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=12, ge=1, le=40)


SPECS = {
    "diagnose_workspace": (Empty, "Inspect source entrypoints, registered definitions and root staging problems without running code. Saved files are not built geometry. Does not modify or lock parts."),
    "read_working_memory": (ReadWorkingMemory, "Read older retained tool outcomes, newest first. The most recent 12 are already supplied in context; use offset 12 for older outcomes. Offered when older outcomes exist. This is previous evidence with source provenance, not current validation."),
    "update_working_notes": (WorkingNotes, "Save durable task notes, decisions, pending requirements and failed approaches. Supplied fields replace only those fields; omitted fields are preserved. These are agent-reported notes, not validation. Does not edit or lock parts."),
    "read_file": (ReadSource, "Read a private source file before editing it. CAD can selectively retrieve unsaved rejected proposal source with version=rejected."),
    "search_files": (Search, "Search private workspace files by literal text."),
    "apply_changes": (ApplyChanges, "Atomically stage a focused edit. Prefer mergeById=true to upsert changed definitions/occurrences while preserving others; use patches for exact text edits or files for full source. dryRun=true previews validation without saving. Omitted manifest fields remain unchanged. No code is executed."),
    "update_parameters": (UpdateParameters, "Atomically change existing named component parameters without rewriting source or assembly relationships. Read the component source first. New parameters require apply_changes. Rebuild and validate before publication."),
    "respond_to_review": (ReviewResponse, "Read-only response when a specific repair finding contradicts current validated evidence. Return the unchanged validated candidate to its reviewer for reassessment once per finding. Does not edit, validate or publish geometry."),
    "build": (Build, "Build the complete saved CAD root. final=false tests an assembly milestone; final=true sends the complete design for review/publication. Do not supply componentId. Use test_component for isolated part diagnostics."),
    "test_component": (ComponentTest, "Test one part/surface and dependencies in isolation. Returns scoped evidence only; never publishes or validates the complete design. Use build(final=true) without componentId for final review."),
    "inspect_geometry": (InspectGeometry, "Inspect the current candidate's build report and requirement evidence. CAD may supply componentId to retrieve a scoped part-probe report with freshness labels."),
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
    "cad": ("read_file", "search_files", "apply_changes", "update_parameters", "respond_to_review", "build", "test_component", "inspect_geometry", "request_engineering", "ask_user", "finish"),
    "engineering": ("read_file", "search_files", "apply_changes", "calculate", "inspect_geometry", "ask_user", "finish"),
}


def json_schema(schema: dict) -> dict:
    """Expand local references without deleting the validation contract.

    Homogeneous tuples use items/minItems/maxItems for chat-completion hosts
    using an older JSON Schema draft. Nulls, bounds and patterns stay explicit.
    This does not opt a provider into strict decoding that it may not support.
    """
    definitions = schema.get("$defs", {})

    def expand(value):
        if isinstance(value, list):
            return [expand(item) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            return expand({**definitions[value["$ref"].rsplit("/", 1)[-1]],
                           **{k: v for k, v in value.items() if k != "$ref"}})
        result = {}
        for key, child in value.items():
            if key in {"$defs", "title", "default"}:
                continue
            if key in {"properties", "patternProperties"}:
                # Field names are data, including names such as title/default.
                result[key] = {name: expand(item) for name, item in child.items()}
            else:
                result[key] = expand(child)
        if "const" in result:
            result["enum"] = [result.pop("const")]
        prefix = result.pop("prefixItems", None)
        if prefix:
            if not all(item == prefix[0] for item in prefix):
                raise ValueError("Tool vectors must have homogeneous element schemas.")
            result["items"] = prefix[0]
            result["minItems"] = result["maxItems"] = len(prefix)
        return result
    return expand(schema)


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
        # Unsupported constraints must still be explained to the model; a
        # broad declaration followed by narrower validation creates blind retries.
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
        descriptions = [value.get("description", "")]
        for key, label in {"pattern": "Must match regex", "minimum": "Minimum", "maximum": "Maximum",
                           "exclusiveMinimum": "Must be greater than", "exclusiveMaximum": "Must be less than",
                           "minLength": "Minimum characters", "maxLength": "Maximum characters",
                           "minItems": "Minimum items", "maxItems": "Maximum items",
                           "maxProperties": "Maximum entries"}.items():
            if key in value:
                descriptions.append(f"{label}: {value[key]}.")
        if "const" in value:
            descriptions.append(f"Only accepted value: {value['const']}.")
        if value.get("type") == "array" and value.get("minItems") == value.get("maxItems") and value.get("minItems"):
            descriptions.append(f"Exactly {value['minItems']} numeric values in order; do not send a shorter vector.")
        if value.get("additionalProperties") is False:
            descriptions.append("Do not add unspecified fields.")
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
                    nullable["description"] = " ".join(filter(None, [*descriptions,
                        nullable.get("description", ""), "May be omitted or null; do not substitute zero for unknown values."]))
                return nullable
            # Heterogeneous unions (for example manifest parameter values)
            # cannot be expressed in Gemini's function schema subset.  Leave
            # an unconstrained value and let the strict Pydantic contract
            # reject malformed arguments with a repairable validation error.
            types = [branch.get("type", "value") for branch in branches if isinstance(branch, dict)]
            result = {"description": " ".join(filter(None, [*descriptions, "Accepted types: " + ", ".join(types) + "."]))}
        elif any(descriptions):
            result["description"] = " ".join(filter(None, descriptions))
        # Pydantic represents fixed-length tuples as ``prefixItems``. Google
        # Gemini's function declaration schema accepts only a homogeneous
        # ``items`` schema for arrays, even when minItems/maxItems retain the
        # tuple length. All Forma tuple fields are homogeneous numeric vectors.
        if "prefixItems" in result and "items" not in result:
            prefix = result.pop("prefixItems")
            if prefix:
                result["items"] = prefix[0]
                # Bounds are stripped for Gemini's schema subset, but losing
                # tuple arity entirely makes other providers emit [thickness]
                # for a three-axis bounding box. Keep the contract explicit in
                # the portable description without adding unsupported keywords.
                result["description"] = (result.get("description", "") +
                    f" Exactly {len(prefix)} numeric values in order; do not send a shorter vector.").strip()
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


def provider_tools(config: dict, tools: list[dict]) -> list[dict]:
    """Only Gemini receives the restricted presentation; other hosts keep JSON Schema."""
    from urllib.parse import urlsplit
    gemini = ("gemini" in str(config.get("model_id", "")).lower()
              or urlsplit(config.get("base_url") or "").hostname == "generativelanguage.googleapis.com")
    if not gemini:
        return tools
    result = deepcopy(tools)
    for tool in result:
        if tool.get("type") == "function":
            tool["function"]["parameters"] = portable_schema(tool["function"]["parameters"])
    return result


def model_tools(role: Role, *, review_response: bool = False, working_memory: bool = False) -> list[dict]:
    names = (*ROLE_TOOLS[role], "update_working_notes", "read_working_memory", "diagnose_workspace") if working_memory and role in {"cad", "coordinator"} else ROLE_TOOLS[role]
    return [{"type": "function", "function": {"name": name, "description": SPECS[name][1],
            "parameters": json_schema((FullBuild if name == "build" else SPECS[name][0]).model_json_schema())}} for name in names
            if name != "respond_to_review" or review_response]


def parse_tool(role: Role, name: str, value: dict):
    if name not in ROLE_TOOLS[role] and not (name in {"update_working_notes", "read_working_memory", "diagnose_workspace"} and role in {"cad", "coordinator"}):
        raise ValueError("This role cannot use that tool.")
    parsed = SPECS[name][0].model_validate(value)
    if isinstance(parsed, ReadFile):
        safe_path(parsed.path)
    return parsed
