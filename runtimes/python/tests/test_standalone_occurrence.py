import sys
from pathlib import Path
import cadquery as cq
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parents[1]))
from assembly_state import verify_step_occurrences


def manifest():
    return {"components":[{"id":"part","kind":"solid"}],"rootComponentId":"part",
        "instances":[{"id":"one","definitionId":"part","parentId":None}]}


def test_single_occurrence_is_verified_by_geometry_instead_of_requiring_assembly():
    shape = cq.Workplane("XY").box(10,20,5).val()
    result = verify_step_occurrences({"part":shape},manifest(),{"one":np.eye(4)})
    assert result["validatedOccurrences"] == ["one"] and result["solidChecks"] == 1


def test_single_occurrence_wrong_pose_still_fails():
    shape = cq.Workplane("XY").box(10,20,5).val()
    world = np.eye(4)
    world[0,3] = 1
    with pytest.raises(ValueError,match="placements or geometry disagree"):
        verify_step_occurrences({"part":shape},manifest(),{"one":world})


def test_multiple_occurrences_still_require_exported_assembly():
    shape = cq.Workplane("XY").box(10,20,5).val()
    value = manifest()
    value["instances"].append({"id":"two","definitionId":"part","parentId":None})
    with pytest.raises(ValueError,match="require a real exported assembly root"):
        verify_step_occurrences({"part":shape},value,{"one":np.eye(4),"two":np.eye(4)})


def test_native_joints_are_not_accepted_as_standalone_shortcut():
    shape = cq.Workplane("XY").box(10,20,5).val()
    value = manifest()
    value["nativeAssembly"] = {"solver":"ondsel"}
    with pytest.raises(ValueError,match="require a real exported assembly root"):
        verify_step_occurrences({"part":shape},value,{"one":np.eye(4)})
