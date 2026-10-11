"""Report statically incompatible CAD entrypoints without executing source."""
import ast


def component_entrypoint_issues(manifest: dict, files: dict) -> list[dict]:
    issues = []
    seen = set()
    for component in manifest.get("components", []):
        path = component.get("source")
        if path in seen or path not in files:
            continue
        seen.add(path)
        try:
            tree = ast.parse(files[path], filename=path)
        except (SyntaxError, TypeError):
            # Syntax diagnostics and dynamic/imported entrypoints are handled
            # elsewhere. Only reject a known incompatible declared function.
            continue
        functions = [node for node in tree.body
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                     and node.name == "build"]
        if not functions:
            continue
        function = functions[-1]
        if function.decorator_list:
            continue
        # A later binding may replace/wrap this function. Do not pretend to
        # know the runtime signature of those dynamic entrypoints.
        later = tree.body[tree.body.index(function) + 1:]
        def binds_build(node):
            if isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                return any(isinstance(item, ast.Name) and item.id == "build"
                           for target in targets for item in ast.walk(target))
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                return any((alias.asname or alias.name.split(".")[0]) == "build"
                           for alias in node.names)
            return False
        if any(binds_build(node) for node in later):
            continue
        args = function.args
        positional = len(args.posonlyargs) + len(args.args)
        required = positional - len(args.defaults)
        accepts_two = required <= 2 and (positional >= 2 or args.vararg is not None)
        required_keywords = any(default is None for default in args.kw_defaults)
        if accepts_two and not required_keywords and not isinstance(function, ast.AsyncFunctionDef):
            continue
        issues.append({"code": "build_signature", "path": path,
            "line": function.lineno,
            "message": f"{path}:{function.lineno}: build must synchronously accept two positional arguments.",
            "repair": "Use def build(parameters: dict, dependencies: dict). The runtime supplies both dictionaries; do not remove dependencies while repairing geometry."})
    return issues


def validate_component_entrypoints(manifest: dict, files: dict) -> None:
    issues = component_entrypoint_issues(manifest, files)
    if issues:
        raise ValueError("CAD entrypoint contract: " + "; ".join(
            issue["message"] for issue in issues[:8]))
