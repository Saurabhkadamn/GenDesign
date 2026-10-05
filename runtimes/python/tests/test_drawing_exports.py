"""Reopen vector exports and verify measured values, units and annotation safety."""

import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

import ezdxf
import pytest
from test_drawings import cylinder_ref, drawings, plate, sheet_spec

spec = importlib.util.spec_from_file_location(
    "forma_test_drawing_export", Path(__file__).parents[1] / "drawing_export.py"
)
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def document(spec=None):
    sheet = drawings.generate_sheet(
        plate(), {"id": "plate"}, spec or sheet_spec(), {"revision": "test"}
    )
    return {"sheets": [sheet]}


def test_round_trip_exports_have_native_dimensions_and_model_units(tmp_path):
    result = document()
    artifacts = exporter.export_drawings(result, tmp_path)
    assert {a["name"].split(".")[-1] for a in artifacts} == {"svg", "pdf", "dxf"}, (
        result["sheets"][0]["issues"]
    )
    assert ET.parse(tmp_path / "drawing-plate.svg").getroot().tag.endswith("svg")
    assert (tmp_path / "drawing-plate.pdf").read_bytes().startswith(b"%PDF-")
    dxf = ezdxf.readfile(tmp_path / "drawing-plate.dxf")
    assert not dxf.audit().errors
    assert dxf.units == 4
    entities = list(dxf.modelspace().query("DIMENSION"))
    sheet = result["sheets"][0]
    assert len(entities) == len(sheet["dimensions"])
    expected = {d["id"]: d for d in sheet["dimensions"]}
    for entity in entities:
        identity, value, unit = [tag.value for tag in entity.get_xdata("FORMA")]
        assert value == pytest.approx(expected[identity]["value"], abs=1e-9)
        assert unit == "mm"
        assert entity.get_measurement() / sheet["scale"] == pytest.approx(
            value, abs=1e-7
        )
        assert entity.dxf.geometry  # Editable DIMENSION with a rendered block.
        assert entity.dxf.text=='<>'


def test_dxf_tolerance_parameters_and_endpoint_regeneration(tmp_path):
    spec=sheet_spec(autoDimensions=False,dimensions=[{'id':'width','kind':'extent','axis':'X','viewId':'top',
        'upperTolerance':.1,'lowerTolerance':.1}])
    result=document(spec)
    assert len(exporter.export_drawings(result,tmp_path))==3
    dxf=ezdxf.readfile(tmp_path/'drawing-plate.dxf')
    dimension=next(iter(dxf.modelspace().query('DIMENSION')))
    style=dimension.override()
    assert style.get('dimtol')==1 and style.get('dimtp')==pytest.approx(.1) and style.get('dimtm')==pytest.approx(.1)
    assert dimension.dxf.text=='<>'
    from ezdxf.math import Vec3
    dimension.dxf.defpoint3=dimension.dxf.defpoint3+Vec3(2*result['sheets'][0]['scale'],0,0)
    assert dimension.get_measurement()/result['sheets'][0]['scale']==pytest.approx(34)
    dimension.override().render()
    rendered=' '.join(e.dxf.text for e in dxf.blocks[dimension.dxf.geometry] if e.dxftype() in {'TEXT','MTEXT'})
    assert '34' in rendered and '32' not in rendered


def test_dxf_basic_dimension_has_frame_and_typed_intent(tmp_path):
    result=document(sheet_spec(autoDimensions=False,dimensions=[{'id':'width','kind':'extent','axis':'X','viewId':'top','basic':True}]))
    assert len(exporter.export_drawings(result,tmp_path))==3
    dxf=ezdxf.readfile(tmp_path/'drawing-plate.dxf')
    dimension=next(iter(dxf.modelspace().query('DIMENSION')))
    assert dimension.get_xdata('FORMA_INTENT')[0].value=='basic'
    assert any(e.dxftype()=='LWPOLYLINE' and e.closed for e in dxf.blocks[dimension.dxf.geometry])


def test_gdt_symbols_are_vectors_and_frames_attach_to_geometry(tmp_path):
    spec = sheet_spec(
        autoDimensions=False,
        datums=[
            {
                "label": "A",
                "viewId": "front",
                "reference": {
                    "kind": "plane",
                    "origin": [0, 0, -2],
                    "direction": [0, 0, 1],
                },
            }
        ],
        controls=[
            {
                "id": "position",
                "viewId": "top",
                "characteristic": "position",
                "reference": cylinder_ref(),
                "toleranceMm": 0.1,
                "datums": ["A"],
                "zone": "diameter",
                "materialCondition": "MMC",
            }
        ],
    )
    result = document(spec)
    assert len(exporter.export_drawings(result, tmp_path)) == 3, result["sheets"][0][
        "issues"
    ]
    sheet = result["sheets"][0]
    assert any(p.get("symbol") == "position" for p in sheet["primitives"])
    assert not any(
        "⌖" in p.get("text", "") or "Ⓜ" in p.get("text", "")
        for p in sheet["primitives"]
    )
    datum = sheet["datums"][0]
    view = next(v for v in sheet["views"] if v["id"] == datum["viewId"])
    point = datum["feature"]["surfacePoint"]
    transform = view["sheetTransform"]
    import numpy as np

    x = transform["x"] + transform["scale"] * np.dot(point, view["basis"]["horizontal"])
    y = transform["y"] - transform["scale"] * np.dot(point, view["basis"]["vertical"])
    assert any(
        p["type"] == "line"
        and p["x1"] == pytest.approx(x)
        and p["y1"] == pytest.approx(y)
        for p in sheet["primitives"]
    )


@pytest.mark.parametrize(
    "change",
    [
        {"scale": 100},
        {"notes": ["Note " + str(i) for i in range(20)]},
        {"notes": ["Unsupported \U0001f9d0"]},
    ],
)
def test_invalid_layout_or_missing_glyph_blocks_all_exports(tmp_path, change):
    result = document(sheet_spec(**change))
    assert not exporter.export_drawings(result, tmp_path)
    assert result["status"] == "unresolved"
    assert not result["sheets"][0]["releaseEligible"]
    assert not list(tmp_path.iterdir())


def test_native_section_hatching_is_exported(tmp_path):
    result = document(
        sheet_spec(
            autoDimensions=False,
            views=[
                {
                    "id": "cut",
                    "kind": "section",
                    "sectionAxis": "Z",
                    "sectionOffsetMm": 0,
                }
            ],
        )
    )
    assert len(exporter.export_drawings(result, tmp_path)) == 3, result["sheets"][0][
        "issues"
    ]
    assert any(p.get("hatch") for p in result["sheets"][0]["primitives"])


def test_removed_reference_never_creates_export(tmp_path):
    spec = sheet_spec(
        autoDimensions=False,
        dimensions=[
            {
                "id": "bore",
                "viewId": "top",
                "kind": "diameter",
                "reference": {**cylinder_ref(), "origin": [100, 0, 0]},
            }
        ],
    )
    result = document(spec)
    assert not exporter.export_drawings(result, tmp_path)
    assert result["status"] == "unresolved" and not list(tmp_path.iterdir())


def test_fresh_step_validator_generates_bound_drawing_artifacts_without_source(
    tmp_path,
):
    import json

    import cadquery as cq
    from test_runtime import validate

    inputs = tmp_path / "accepted"
    inputs.mkdir()
    output = tmp_path / "verified"
    output.mkdir()
    spec = sheet_spec()
    manifest = {
        "schemaVersion": 1,
        "units": "mm",
        "rootComponentId": "plate",
        "instances": [],
        "components": [
            {
                "id": "plate",
                "name": "Plate",
                "kind": "solid",
                "source": "parts/missing.py",
                "dependencies": [],
                "parameters": {},
                "color": "#b8c9a5",
            }
        ],
        "drawings": [spec],
    }
    cq.exporters.export(plate(), str(inputs / "plate.step"))
    identity = {"candidate": "native-step-qualification"}
    for name, value in [
        ("manifest", manifest),
        ("requirements", []),
        ("identity", identity),
    ]:
        (inputs / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
    validate(inputs, output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["identity"] == identity and report["drawings"]["identity"] == identity
    assert len([a for a in report["artifacts"] if a["kind"] == "drawing"]) == 4
    assert report["drawings"]["status"] == "draft"
    assert not list(inputs.rglob("*.py"))


def test_sixty_occurrence_drawing_bom_and_balloon_share_validated_identity(tmp_path):
    import json

    import cadquery as cq
    from test_runtime import validate

    inputs = tmp_path / "accepted"
    inputs.mkdir()
    output = tmp_path / "verified"
    output.mkdir()
    shape = cq.Workplane("XY").box(8, 8, 4).faces(">Z").workplane().hole(2).val()
    assembly = cq.Assembly(name="assembly")
    instances = []
    occurrences = {}
    for index in range(60):
        iid = f"plate_{index}"
        position = [index % 10 * 12, index // 10 * 12, 0]
        loc = cq.Location(cq.Vector(*position))
        assembly.add(shape, name=iid, loc=loc)
        instances.append(
            {
                "id": iid,
                "definitionId": "plate",
                "parentId": None,
                "name": iid,
                "frame": {"position": position, "rotation": [0, 0, 0]},
            }
        )
        occurrences[iid] = shape.moved(loc)
    spec = sheet_spec(
        id="assembly_sheet",
        componentId="assembly",
        autoDimensions=False,
        includeBom=True,
    )
    definitions = [
        {
            "id": "plate",
            "name": "Plate",
            "kind": "solid",
            "source": "parts/missing.py",
            "dependencies": [],
            "parameters": {},
            "color": "#b8c9a5",
            "partMetadata": {"partNumber": "QUAL-PLATE", "revision": "TEST"},
        },
        {
            "id": "assembly",
            "name": "Grid",
            "kind": "assembly",
            "source": "assemblies/missing.py",
            "dependencies": ["plate"],
            "parameters": {},
            "color": "#b8c9a5",
        },
    ]
    manifest = {
        "schemaVersion": 1,
        "units": "mm",
        "rootComponentId": "assembly",
        "instances": instances,
        "components": definitions,
        "drawings": [spec],
    }
    cq.exporters.export(shape, str(inputs / "plate.step"))
    cq.exporters.export(assembly.toCompound(), str(inputs / "assembly.step"))
    identity = {"candidate": "sixty-accepted-occurrences"}
    for name, value in [
        ("manifest", manifest),
        ("requirements", []),
        ("identity", identity),
    ]:
        (inputs / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
    validate(inputs, output)
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["assemblyPlacement"]["solidChecks"] == 60
    sheet = report["drawings"]["sheets"][0]
    assert sheet["status"] == "draft", sheet["issues"]
    assert (
        sheet["bom"]["identity"] == identity
        and sheet["bom"]["flat"][0]["quantity"] == 60
    )
    balloon = sheet["balloons"][0]
    assert (
        balloon["item"] == "1"
        and balloon["quantity"] == 60
        and len(balloon["occurrenceIds"]) == 60
    )
    actual = occurrences[balloon["anchorOccurrenceId"]]
    assert any(
        v.Center().toTuple() == pytest.approx(balloon["anchor"], abs=1e-6)
        for v in actual.Vertices()
    )
    assert (output / "drawing-assembly_sheet.pdf").exists()


def test_plane_angle_exports_as_native_angular_dimension(tmp_path):
    import cadquery as cq
    shape=cq.Workplane('XZ').polyline([(0,0),(20,0),(20,20)]).close().extrude(10).val()
    features=drawings.feature_inventory(shape)
    base=next(f for f in features if f['kind']=='plane' and abs(f['direction'][2])>1-1e-8)
    slope=next(f for f in features if f['kind']=='plane' and abs(f['direction'][0])>.6 and abs(f['direction'][2])>.6)
    ref=lambda f:{k:f[k] for k in ('kind','origin','direction')}
    spec=sheet_spec(autoDimensions=False,views=[{'id':'front','kind':'front'}],dimensions=[{
        'id':'angle','kind':'angle','viewId':'front','reference':ref(base),'secondReference':ref(slope),'offsetMm':10}])
    result={'sheets':[drawings.generate_sheet(shape,{'id':'plate'},spec,{})]}
    assert len(exporter.export_drawings(result,tmp_path))==3,result['sheets'][0]['issues']
    dxf=ezdxf.readfile(tmp_path/'drawing-plate.dxf')
    dimension=next(iter(dxf.modelspace().query('DIMENSION')))
    assert dimension.dimtype==2
    assert dimension.get_measurement()==pytest.approx(45,abs=1e-7)
    assert dimension.get_xdata('FORMA')[1].value==pytest.approx(45,abs=1e-7)
