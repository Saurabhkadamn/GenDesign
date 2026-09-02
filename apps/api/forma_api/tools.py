from typing import Literal

from pydantic import Field, field_validator

from .contracts import Contract, Manifest, Requirement, Role, safe_path


class Empty(Contract):
    pass


class ReadFile(Contract):
    path: str


class Search(Contract):
    query: str = Field(min_length=1, max_length=200)


class ApplyChanges(Contract):
    files: dict[str, str]
    manifest: Manifest | None = None

    @field_validator("files", mode="before")
    @classmethod
    def decode_file_entries(cls, value):
        if isinstance(value, list):
            return {item["path"]: item["content"] for item in value
                    if isinstance(item, dict) and "path" in item and "content" in item}
        return value


class Delegate(Contract):
    role: Literal["cad", "engineering"]
    task: str = Field(min_length=1, max_length=6000)
    requirements: list[Requirement] = Field(max_length=100)


class Publish(Contract):
    summary: str = Field(min_length=1, max_length=1000)


class Restore(Publish):
    revisionId: str


class Question(Contract):
    question: str = Field(min_length=1, max_length=3000)


class Finish(Contract):
    message: str = Field(min_length=1, max_length=8000)


SPECS = {
    "read_file": (ReadFile, "Read a private workspace source file before editing it."),
    "search_files": (Search, "Search private workspace files by literal text."),
    "apply_changes": (ApplyChanges, "Atomically stage related files and an optional complete manifest. Does not execute code."),
    "build": (Empty, "Build STEP geometry and verify artifact integrity. Report any available requirement evidence for human review."),
    "inspect_geometry": (Empty, "Inspect the current candidate's build report and optional requirement evidence."),
    "calculate": (ReadFile, "Execute a calculations/ module twice in separate Python processes."),
    "delegate": (Delegate, "Delegate a complete task and explicit requirements to one specialist."),
    "publish_revision": (Publish, "Publish the exact successfully built candidate as a draft for human review."),
    "restore_revision": (Restore, "Load an existing owned revision as the candidate; it must be rebuilt before publication."),
    "ask_user": (Question, "Pause and ask for missing information or explain an unsupported requirement."),
    "finish": (Finish, "Finish with a factual answer supported by completed tool results."),
}
ROLE_TOOLS = {
    "coordinator": ("delegate", "inspect_geometry", "publish_revision", "restore_revision", "ask_user", "finish"),
    "cad": ("read_file", "search_files", "apply_changes", "build", "inspect_geometry", "ask_user", "finish"),
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
        allowed = {"type", "description", "enum", "items", "properties", "required"}
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


def model_tools(role: Role) -> list[dict]:
    return [{"type": "function", "function": {"name": name, "description": SPECS[name][1],
            "parameters": portable_schema(SPECS[name][0].model_json_schema())}} for name in ROLE_TOOLS[role]]


def parse_tool(role: Role, name: str, value: dict):
    if name not in ROLE_TOOLS[role]:
        raise ValueError("This role cannot use that tool.")
    parsed = SPECS[name][0].model_validate(value)
    if isinstance(parsed, ReadFile):
        safe_path(parsed.path)
    return parsed
