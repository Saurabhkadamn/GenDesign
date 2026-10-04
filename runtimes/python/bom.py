"""Deterministic engineering BOM from a validated occurrence inventory."""
from __future__ import annotations

from collections import Counter, defaultdict
import csv
import hashlib
import io
import json


def generate_bom(manifest, *, validated_occurrences, identity=None, configuration="as_built"):
    definitions = {d["id"]: d for d in manifest["components"]}
    instances = {i["id"]: i for i in manifest.get("instances", [])}
    if len(definitions) != len(manifest["components"]) or len(instances) != len(manifest.get("instances", [])):
        raise ValueError("Duplicate BOM identity")
    children = defaultdict(list)
    for instance in instances.values():
        if instance["definitionId"] not in definitions:
            raise ValueError("Unknown BOM definition")
        parent = instance.get("parentId")
        if parent is not None and parent not in instances:
            raise ValueError("Unknown BOM parent")
        children[parent].append(instance["id"])
    if not instances:
        root = manifest.get("rootComponentId")
        if not root or root not in definitions:
            raise ValueError("BOM needs a validated root component")
        if definitions[root]["kind"] == "assembly":
            raise ValueError("Assembly BOM needs explicit occurrence inventory")
        instances[root] = {"id": root, "definitionId": root, "parentId": None}
        children[None] = [root]
    visiting, reached = set(), set()

    def visit(iid, depth=0):
        if depth > 32 or iid in visiting:
            raise ValueError("Invalid or excessively deep BOM hierarchy")
        visiting.add(iid)
        reached.add(iid)
        for child in children[iid]:
            visit(child, depth + 1)
        visiting.remove(iid)
    for root in children[None]:
        visit(root)
    if reached != set(instances):
        raise ValueError("Cyclic or disconnected BOM hierarchy")
    physical = {iid for iid, i in instances.items() if not children[iid]
                and definitions[i["definitionId"]]["kind"] != "surface"}
    if set(validated_occurrences) != physical:
        raise ValueError("BOM occurrence population does not match independently validated geometry")

    def definition(iid):
        return definitions[instances[iid]["definitionId"]]

    def excluded(iid):
        return (instances[iid].get("bomExclude", False)
                or definition(iid).get("bomBehavior", "normal") == "reference"
                or definition(iid)["kind"] == "surface")

    def metadata(cid):
        d = definitions[cid]
        m = d.get("partMetadata") or {}
        material = d.get("material") or {}
        return {"definitionId": cid, "partNumber": m.get("partNumber", ""),
                "revision": m.get("revision", ""), "variant": m.get("variant", ""),
                "description": m.get("description") or d["name"],
                "material": material.get("name", ""), "kind": d["kind"],
                "bomBehavior": d.get("bomBehavior", "normal"), "unit": "ea"}

    def signature(iid):
        # Assemblies with different effective child inventories cannot share
        # one structured row, even when they reuse a definition.
        effective = [] if definition(iid).get("bomBehavior") == "purchased" else [
            signature(child) for child in children[iid] if not excluded(child)]
        return (instances[iid]["definitionId"], tuple(sorted(effective)))

    def visible(iids):
        for iid in sorted(iids):
            if excluded(iid):
                continue
            if definition(iid).get("bomBehavior") == "phantom":
                yield from visible(children[iid])
            else:
                yield iid

    structured, flat_counts, flat_instances = [], Counter(), defaultdict(list)

    def structured_rows(iids, prefix="", parent_quantity=1):
        groups = defaultdict(list)
        for iid in visible(iids):
            groups[signature(iid)].append(iid)
        ordered = sorted(groups.values(), key=lambda g: (
            metadata(instances[g[0]]["definitionId"])["partNumber"], instances[g[0]]["definitionId"], g[0]))
        for index, group in enumerate(ordered, 1):
            representative = group[0]
            cid = instances[representative]["definitionId"]
            item = f"{prefix}.{index}" if prefix else str(index)
            if len(group) % parent_quantity:
                raise ValueError("Structured BOM has inconsistent repeated subassembly inventory")
            structured.append({**metadata(cid), "item": item,
                "quantity": len(group) // parent_quantity, "totalQuantity": len(group),
                "occurrenceIds": sorted(group)})
            if definition(representative).get("bomBehavior") != "purchased":
                structured_rows([child for iid in group for child in children[iid]], item, len(group))

    def rollup(iid):
        if excluded(iid):
            return
        d = definition(iid)
        if children[iid] and d.get("bomBehavior") != "purchased":
            for child in children[iid]:
                rollup(child)
        elif d.get("bomBehavior") != "phantom":
            cid = instances[iid]["definitionId"]
            flat_counts[cid] += 1
            flat_instances[cid].append(iid)
    structured_rows(children[None])
    for iid in children[None]:
        rollup(iid)
    flat = [{**metadata(cid), "quantity": flat_counts[cid], "occurrenceIds": sorted(flat_instances[cid])}
            for cid in sorted(flat_counts, key=lambda c: (metadata(c)["partNumber"], c))]
    issues = []
    numbers = defaultdict(list)
    for row in flat:
        if not row["partNumber"] or not row["revision"]:
            issues.append({"definitionId": row["definitionId"], "code": "unassigned_part_identity",
                           "message": "Part number or revision has not been assigned by an engineer."})
        if row["partNumber"]:
            numbers[(row["partNumber"], row["revision"], row["variant"])].append(row["definitionId"])
    for key, ids in numbers.items():
        if len(ids) > 1:
            issues.append({"definitionIds": sorted(ids), "code": "ambiguous_part_identity",
                           "message": "Multiple definitions share a part number, revision and variant; review before procurement."})
    source_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"),
                                            allow_nan=False).encode()).hexdigest()
    return {"schemaVersion": 1, "configuration": configuration, "status": "incomplete" if issues else "draft",
            "identity": identity or {}, "manifestSha256": source_hash, "structured": structured,
            "flat": flat, "issues": issues,
            "limitations": ["Occurrence counts in ea; no cut-list or process/material consumption calculation.",
                            "Draft BOM; engineering release and procurement approval are separate workflows."]}


def csv_export(bom, mode="flat"):
    if mode not in {"flat", "structured"}:
        raise ValueError("Unknown BOM export mode")
    fields = (["item"] if mode == "structured" else []) + [
        "partNumber", "revision", "variant", "description", "quantity", "unit", "material", "definitionId"]
    if mode == "structured":
        fields.append("totalQuantity")
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in bom[mode]:
        safe = dict(row)
        # Spreadsheet apps can execute formulas in imported CSV text cells.
        for field in fields:
            if isinstance(safe.get(field), str) and safe[field].lstrip().startswith(("=", "+", "-", "@")):
                safe[field] = "'" + safe[field]
        writer.writerow(safe)
    return buffer.getvalue()
