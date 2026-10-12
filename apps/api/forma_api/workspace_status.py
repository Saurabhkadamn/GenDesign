"""Read-only staging diagnostics, distinct from executed CAD validation."""
import ast


def workspace_status(snapshot: dict) -> dict:
    manifest = snapshot.get("manifest") or {}
    components = manifest.get("components") or []
    root_id = manifest.get("rootComponentId")
    root = next((item for item in components if item.get("id") == root_id), None)
    sources = {item.get("source") for item in components}
    by_source = {item.get("source"):item for item in components}
    diagnostics = []
    for path, source in snapshot.get("files", {}).items():
        if not path.startswith(("parts/", "assemblies/")):
            continue
        try:
            tree = ast.parse(source, filename=path)
            functions = [item.name for item in tree.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
            imported = any(isinstance(item, (ast.Import, ast.ImportFrom)) and
                any((alias.asname or alias.name) == "build" for alias in item.names) for item in tree.body)
            rebound = any(isinstance(item, (ast.Assign, ast.AnnAssign)) and
                any(isinstance(target, ast.Name) and target.id == "build" for target in
                    (item.targets if isinstance(item, ast.Assign) else [item.target])) for item in tree.body)
            entrypoint = "declared" if "build" in functions else "runtime_unchecked" if imported or rebound else "missing"
            literal_reads = set()
            for function in tree.body:
                if isinstance(function,(ast.FunctionDef,ast.AsyncFunctionDef)) and function.name == "build":
                    args = [*function.args.posonlyargs,*function.args.args]
                    if args:
                        literal_reads.update(node.slice.value for node in ast.walk(function)
                            if isinstance(node,ast.Subscript) and isinstance(node.value,ast.Name)
                            and node.value.id == args[0].arg and isinstance(node.slice,ast.Constant)
                            and isinstance(node.slice.value,str))
            definition = by_source.get(path)
            parameter_keys = sorted((definition.get("parameters") or {}).keys()) if definition else []
            diagnostics.append({"path": path, "registered": path in sources,
                "entrypoint": entrypoint, "topLevelFunctions": functions[:20],
                "declaredParameterKeys":parameter_keys, "literalParameterReads":sorted(literal_reads),
                "referencedMissingParameters":sorted(literal_reads-set(parameter_keys)) if definition else []})
        except SyntaxError as exc:
            diagnostics.append({"path":path, "registered":path in sources,
                "entrypoint":"syntax_error", "line":exc.lineno})
    root_source_present = bool(root and root.get("source") in snapshot.get("files", {}))
    problems = []
    if not root:
        problems.append("No manifest root component is defined. Send the manifest as apply_changes.manifest, not as a JSON source file.")
    if root and not root_source_present:
        problems.append("The root component's Python source is missing.")
    if any(item["entrypoint"] == "missing" for item in diagnostics):
        problems.append("Saved source is missing a top-level build(parameters, dependencies) entrypoint. A script, another function name or a quoted source literal does not define that callable.")
    if any(not item["registered"] for item in diagnostics):
        problems.append("Some geometry source files have no manifest component definition. Register them before building; saved files are not built parts.")
    if any(item.get("referencedMissingParameters") for item in diagnostics):
        problems.append("Some build functions index literal parameter names absent from their component's manifest parameters. Inspect these reads and supply the requested values or correct the source. Reads may be conditional; this is an advisory observation, not proof of a runtime failure.")
    return {"componentCount":len(components), "instanceCount":len(manifest.get("instances") or []),
        "rootComponentId":root_id, "rootSourcePresent":root_source_present,
        "sourceDiagnostics":diagnostics, "stagingProblems":problems,
        "geometryExecuted":False, "instruction":"These are source/staging observations, not geometry validation. Repair the reported workspace structure, then build and inspect."}
