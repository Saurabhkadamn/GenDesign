import json
import sys
from pathlib import Path

import cadquery as cq
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from forma_runtime import build, validate, properties, calculate, module_at


def fixture(tmp_path, kind="solid", source=None):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "parts").mkdir()
    (workspace / "parts" / "plate.py").write_text(
        source
        or "import cadquery as cq\ndef build(p,d):\n return cq.Workplane('XY').box(p['width'],20,5)\n"
    )
    manifest = {
        "schemaVersion": 1,
        "units": "mm",
        "rootComponentId": "plate",
        "instances": [],
        "components": [
            {
                "id": "plate",
                "name": "Plate",
                "source": "parts/plate.py",
                "kind": kind,
                "dependencies": [],
                "parameters": {"width": 40},
                "color": "#b8c9a5",
            }
        ],
    }
    (workspace / "manifest.json").write_text(json.dumps(manifest))
    output = tmp_path / "build"
    output.mkdir()
    return workspace, output, manifest


def test_step_roundtrip_and_real_glb(tmp_path):
    workspace, output, manifest = fixture(tmp_path)
    build(workspace, output)
    (output / "manifest.json").write_text(json.dumps(manifest))
    verified = tmp_path / "verified"
    verified.mkdir()
    validate(output, verified)
    report = json.loads((verified / "report.json").read_text())
    assert report["components"]["plate"]["dimensions"] == pytest.approx([40, 20, 5])
    assert report["components"]["plate"]["volumeMm3"] == pytest.approx(4000)
    assert (verified / "preview.glb").read_bytes()[:4] == b"glTF"
    manifest["components"][0]["parameters"]["width"] = 60
    (workspace / "manifest.json").write_text(json.dumps(manifest))
    build(workspace, output)
    shape = cq.importers.importStep(str(output / "plate.step")).val()
    assert shape.BoundingBox().xlen == pytest.approx(60)


def test_open_surface_is_valid_only_when_declared():
    surface = cq.Face.makePlane(40, 20)
    assert properties(surface, "surface")["faces"] == 1
    with pytest.raises(ValueError, match="closed solid"):
        properties(surface, "solid")


def test_assembly_constraint_solver():
    plate = cq.Workplane("XY").box(10, 10, 2)
    assembly = cq.Assembly(name="stack").add(plate, name="base").add(
        plate, name="lid", loc=cq.Location(cq.Vector(0, 0, 10))
    )
    assembly.constrain("base", "Fixed")
    assembly.constrain("base@faces@>Z", "lid@faces@<Z", "Plane")
    assembly.solve()
    shape = assembly.toCompound()
    assert shape.isValid()
    assert shape.BoundingBox().zlen == pytest.approx(4, abs=1e-4)


def test_paths_cannot_escape_workspace(tmp_path):
    with pytest.raises(ValueError, match="Invalid module path"):
        module_at(tmp_path, "../secrets.py")


def test_clean_process_calculation(tmp_path):
    root = tmp_path / "workspace"
    (root / "calculations").mkdir(parents=True)
    (root / "calculations" / "area.py").write_text(
        "def calculate():\n return {'results': {'area': {'value': 200, 'unit': 'mm^2'}}}\n"
    )
    output = tmp_path / "output"
    output.mkdir()
    calculate(root, output, "calculations/area.py")
    result = json.loads((output / "calculation.json").read_text())
    assert result["reproducible"] is True
    assert result["result"]["results"]["area"]["value"] == 200


def test_assembly_export_and_instance_placements(tmp_path):
    import trimesh

    workspace, output, manifest = fixture(tmp_path)
    (workspace / "assemblies").mkdir()
    (workspace / "assemblies" / "pair.py").write_text(
        "import cadquery as cq\ndef build(p,d):\n"
        " return cq.Assembly(name='pair').add(d['plate'],name='left').add(d['plate'],name='right',loc=cq.Location(cq.Vector(60,0,0)))\n"
    )
    manifest["components"].append(
        {
            "id": "pair",
            "name": "Pair",
            "source": "assemblies/pair.py",
            "kind": "assembly",
            "dependencies": ["plate"],
            "parameters": {},
            "color": "#b8c9a5",
        }
    )
    manifest["rootComponentId"] = "pair"
    manifest["instances"] = [
        {
            "id": "pair-instance",
            "definitionId": "pair",
            "name": "Pair",
            "parentId": None,
            "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]},
        },
        {
            "id": "left",
            "definitionId": "plate",
            "name": "Left",
            "parentId": "pair-instance",
            "frame": {"position": [0, 0, 0], "rotation": [0, 0, 0]},
        },
        {
            "id": "right",
            "definitionId": "plate",
            "name": "Right",
            "parentId": "pair-instance",
            "frame": {"position": [60, 0, 0], "rotation": [0, 0, 0]},
        },
    ]
    (workspace / "manifest.json").write_text(json.dumps(manifest))
    build(workspace, output)
    (output / "manifest.json").write_text(json.dumps(manifest))
    verified = tmp_path / "verified"
    verified.mkdir()
    validate(output, verified)
    report = json.loads((verified / "report.json").read_text())
    assert report["components"]["pair"]["solids"] == 2
    assert report["components"]["pair"]["volumeMm3"] == pytest.approx(8000)
    scene = trimesh.load(verified / "preview.glb", force="scene")
    assert set(scene.graph.nodes_geometry) == {"left", "right"}
    assert scene.extents.tolist() == pytest.approx([100, 20, 5])
    assert (verified / "pair.step").read_bytes() == (output / "pair.step").read_bytes()
    manifest["instances"][2]["frame"]["position"][0] = 80
    (output / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="placements"):
        validate(output, verified)


def test_scientific_runtime_units_and_independent_check(tmp_path):
    root = tmp_path / "workspace"
    (root / "calculations").mkdir(parents=True)
    (root / "calculations" / "integral.py").write_text(
        "from scipy.integrate import quad\nfrom sympy import symbols, integrate\nfrom pint import UnitRegistry\n"
        "def calculate():\n u=UnitRegistry()\n x=symbols('x')\n analytical=float(integrate(x*x,(x,0,2)))\n numerical,error=quad(lambda t:t*t,0,2)\n"
        " return {'lengthMm':(2*u.inch).to('mm').magnitude,'integral':numerical,'checked':abs(analytical-numerical)<1e-10,'error':error}\n"
    )
    output = tmp_path / "output"
    output.mkdir()
    calculate(root, output, "calculations/integral.py")
    result = json.loads((output / "calculation.json").read_text())
    assert result["result"]["lengthMm"] == pytest.approx(50.8)
    assert result["result"]["integral"] == pytest.approx(8 / 3)
    assert result["result"]["checked"] is True
