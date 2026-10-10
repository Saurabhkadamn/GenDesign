"""Actionable, source-free feedback for an atomic workspace edit rejection.

Snapshot remains the authority. These diagnostics expose related blockers at
once, so an agent can repair a staged assembly without guessing the next error.
"""
from pydantic import ValidationError


def workspace_contract_feedback(manifest: dict, files: dict, error: Exception) -> dict:
    issues = []

    def add(code, path, message, repair):
        issues.append({"code": code, "path": path, "message": message, "repair": repair})

    definitions = {item["id"]: item for item in manifest.get("components", [])}
    for item in definitions.values():
        if item["source"] not in files:
            add("missing_source", f"components.{item['id']}.source",
                f"Source is missing: {item['source']}",
                "Include the executable source in files in the same apply_changes action.")
        for dependency in item.get("dependencies", []):
            if dependency not in definitions:
                add("unknown_dependency", f"components.{item['id']}.dependencies.{dependency}",
                    f"Dependency {dependency} has no component definition.",
                    "Stage the dependency definition and source together before referring to it.")
    root = manifest.get("rootComponentId")
    if root and root not in definitions:
        add("unknown_root", "rootComponentId",
            f"Root {root} has no component definition.",
            "Define the assembly component and its assemblies/ source in this edit. "
            "For a single staged part, the root may name that existing part.")
    instances = {item["id"]: item for item in manifest.get("instances", [])}
    for item in instances.values():
        if item["definitionId"] not in definitions:
            add("unknown_definition", f"instances.{item['id']}.definitionId",
                f"Unknown component {item['definitionId']}.",
                "Stage the component and its source before adding an occurrence.")
    native = manifest.get("nativeAssembly")
    if native:
        if root in definitions and definitions[root]["kind"] != "assembly":
            add("native_root", "rootComponentId", "Native solving needs an assembly root.",
                "Supply an assembly component, source and dependencies for the staged parts.")
        parents = {item.get("parentId") for item in instances.values() if item.get("parentId")}
        physical = {iid for iid, item in instances.items()
                    if iid not in parents and item["definitionId"] in definitions
                    and definitions[item["definitionId"]]["kind"] != "surface"}
        for iid in native.get("groundedInstances", []):
            if iid not in physical:
                add("invalid_ground", f"nativeAssembly.groundedInstances.{iid}",
                    f"Ground {iid} is not a physical leaf occurrence.",
                    "Physical parts are siblings, with parentId=null or an actual subassembly "
                    "occurrence. Do not parent a cover to a housing solid to express a mate. "
                    "Ground only the intended physical base; express mates as joints.")
        motion = native.get("motion")
        if motion:
            joints = {item["id"]: item for item in manifest.get("joints", [])}
            if (motion["jointId"] not in joints
                    or joints[motion["jointId"]]["kind"] not in {"revolute", "slider"}):
                add("invalid_motion_joint", "nativeAssembly.motion.jointId",
                    f"Motion joint {motion['jointId']} is not a qualified revolute or slider joint.",
                    "Define the joint, matching occurrence endpoints and component-local references "
                    "before enabling a driver. While staging stationary parts, use motion=null; "
                    "retain the requested motion as unfinished work.")
            if native.get("allowedDof", 0) != 1:
                add("motion_dof", "nativeAssembly.allowedDof",
                    "A single motion driver requires allowedDof=1.",
                    "Declare one intended DOF for the mechanism and provide its constraints. "
                    "A stationary intermediate assembly uses motion=null and allowedDof=0.")
    # Include the authoritative validator's first blocker for cases not covered
    # above. Never echo Pydantic's input_value: it can contain private source.
    errors = (error.errors(include_input=False, include_url=False, include_context=False)
              if isinstance(error, ValidationError) else [{"loc": (), "msg": str(error)}])
    messages = [item["msg"] for item in errors]
    if not issues:
        for item in errors[:20]:
            add("contract", ".".join(map(str, item["loc"])) or "workspace",
                item["msg"][:500], "Correct this field using the current workspace and tool schema.")
    return {"ok": False, "category": "workspace_contract",
            "message": "; ".join(messages)[:1500], "issues": issues[:40],
            "repairGuidance": "The entire edit was rejected; workspace and files are unchanged. "
            "Fix the listed blockers together in one coherent apply_changes action. Include real "
            "source for every new definition. Do not remove requested parts or motion to claim completion."}


def contract_repair_attempt(previous: dict, feedback: dict) -> int:
    """Stop stalled repairs, while allowing real blocker resolution within call budget."""
    before = {(item["code"], item["path"]) for item in previous.get("issues", [])}
    after = {(item["code"], item["path"]) for item in feedback["issues"]}
    if before and before - after:
        return 1
    return previous.get("stalledAttempts", 0) + 1
