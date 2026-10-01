import csv
import io
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from bom import csv_export, generate_bom


def definition(cid, kind="solid", **kwargs):
    return {"id": cid, "name": cid, "kind": kind,
            "partMetadata": {"partNumber": f"PN-{cid}", "revision": "A"}, **kwargs}


def occurrence(iid, cid, parent=None, **kwargs):
    return {"id": iid, "definitionId": cid, "parentId": parent, **kwargs}


def sixty():
    components = [definition("machine", "assembly"), definition("module", "assembly"), definition("bolt")]
    instances = [occurrence("root", "machine")]
    for group in range(6):
        parent = f"module_{group}"
        instances.append(occurrence(parent, "module", "root"))
        instances.extend(occurrence(f"bolt_{group}_{i}", "bolt", parent) for i in range(10))
    return {"components": components, "instances": instances, "rootComponentId": "machine"}


def leaves(manifest):
    parents = {i.get("parentId") for i in manifest["instances"]}
    return [i["id"] for i in manifest["instances"] if i["id"] not in parents]


def test_nested_sixty_occurrence_bom_counts_parts_not_definitions():
    manifest = sixty()
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest), identity={"candidate": "sha"})
    assert bom["flat"][0]["quantity"] == 60
    assert len(bom["flat"]) == 1
    assert [(r["quantity"], r["totalQuantity"]) for r in bom["structured"]] == [(1, 1), (6, 6), (10, 60)]
    assert bom["identity"]["candidate"] == "sha"
    assert bom["status"] == "draft"
    assert bom == generate_bom(manifest, validated_occurrences=list(reversed(leaves(manifest))), identity={"candidate": "sha"})


def test_purchased_subassembly_stops_flat_rollup():
    manifest = sixty()
    manifest["components"][1]["bomBehavior"] = "purchased"
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest))
    assert [(r["definitionId"], r["quantity"]) for r in bom["flat"]] == [("module", 6)]
    assert len(bom["structured"]) == 2


def test_phantom_group_and_exclusion_are_explicit():
    manifest = sixty()
    manifest["components"][1]["bomBehavior"] = "phantom"
    manifest["instances"][2]["bomExclude"] = True
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest))
    assert bom["flat"][0]["quantity"] == 59
    assert [r["definitionId"] for r in bom["structured"]] == ["machine", "bolt"]


def test_missing_engineering_part_identity_is_visible():
    manifest = sixty()
    manifest["components"][2].pop("partMetadata")
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest))
    assert bom["status"] == "incomplete"
    assert bom["flat"][0]["partNumber"] == ""
    assert bom["issues"][0]["code"] == "unassigned_part_identity"


def test_same_names_do_not_merge_different_revisions():
    manifest = {"components": [definition("one", name="Bolt"), definition("two", name="Bolt")],
                "instances": [occurrence("first", "one"), occurrence("second", "two")]}
    manifest["components"][1]["partMetadata"]["revision"] = "B"
    bom = generate_bom(manifest, validated_occurrences=["first", "second"])
    assert len(bom["flat"]) == 2


def test_bom_rejects_unvalidated_or_missing_occurrences():
    manifest = sixty()
    with pytest.raises(ValueError, match="independently validated"):
        generate_bom(manifest, validated_occurrences=leaves(manifest)[:-1])


def test_repeated_assembly_variants_have_separate_structured_rows():
    manifest = sixty()
    manifest["instances"][2]["bomExclude"] = True
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest))
    groups = [r for r in bom["structured"] if r["definitionId"] == "module"]
    assert sorted(r["quantity"] for r in groups) == [1, 5]
    assert bom["flat"][0]["quantity"] == 59


def test_bom_rejects_cyclic_parent_tree():
    manifest = sixty()
    manifest["instances"][0]["parentId"] = "module_0"
    with pytest.raises(ValueError, match="Cyclic"):
        generate_bom(manifest, validated_occurrences=[])


def test_csv_quotes_text_and_prevents_spreadsheet_formula_execution():
    manifest = sixty()
    manifest["components"][2]["partMetadata"]["description"] = '=HYPERLINK("bad", "quoted, description")'
    bom = generate_bom(manifest, validated_occurrences=leaves(manifest))
    rows = list(csv.DictReader(io.StringIO(csv_export(bom))))
    assert rows[0]["description"].startswith("'=")
    assert rows[0]["quantity"] == "60"
    assert ", description" in rows[0]["description"]


def test_single_multibody_part_is_one_bom_item():
    manifest = {"components": [definition("casting")], "instances": [], "rootComponentId": "casting"}
    bom = generate_bom(manifest, validated_occurrences=["casting"])
    assert bom["flat"][0]["quantity"] == 1


def test_assembly_without_occurrence_inventory_cannot_guess_a_bom():
    manifest = {"components": [definition("machine", "assembly")], "instances": [], "rootComponentId": "machine"}
    with pytest.raises(ValueError, match="explicit occurrence"):
        generate_bom(manifest, validated_occurrences=[])
