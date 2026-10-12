from forma_api.workspace_status import workspace_status


def test_script_or_quoted_code_is_not_reported_as_a_buildable_part():
    files = {"parts/a.py":"def make_plate():\n    return None",
             "parts/b.py":repr("def build(parameters, dependencies):\n    return None")}
    snapshot = {"manifest":{"components":[],"rootComponentId":None},"files":files}
    result = workspace_status(snapshot)
    assert result["componentCount"] == 0 and result["geometryExecuted"] is False
    assert all(item["entrypoint"] == "missing" for item in result["sourceDiagnostics"])
    assert result["sourceDiagnostics"][0]["topLevelFunctions"] == ["make_plate"]
    assert len(result["stagingProblems"]) == 3
    assert snapshot["files"] == files


def test_imported_and_dynamic_entrypoints_remain_unchecked_without_execution():
    result = workspace_status({"files":{"parts/a.py":"from dangerous import build",
        "parts/b.py":"build = dangerous()"},"manifest":{}})
    assert {d["entrypoint"] for d in result["sourceDiagnostics"]} == {"runtime_unchecked"}


def test_registered_entrypoint_with_source_is_staged_but_not_validated():
    result = workspace_status({"manifest":{"components":[{"id":"a","source":"parts/a.py"}],
        "rootComponentId":"a"},"files":{"parts/a.py":"def build(parameters, dependencies):\n    return None"}})
    assert result["rootSourcePresent"] is True and not result["stagingProblems"]
    assert result["sourceDiagnostics"][0]["registered"] is True
    assert result["geometryExecuted"] is False


def test_missing_literal_parameter_reads_are_reported_without_execution_or_edits():
    snapshot={"manifest":{"components":[{"id":"a","source":"parts/a.py","parameters":{"height":40}}],
        "rootComponentId":"a"},"files":{"parts/a.py":
        'def build(params, dependencies):\n    return params["width"] + params["height"]'}}
    result=workspace_status(snapshot)
    row=result['sourceDiagnostics'][0]
    assert row['declaredParameterKeys']==['height']
    assert row['literalParameterReads']==['height','width']
    assert row['referencedMissingParameters']==['width']
    assert 'advisory' in result['stagingProblems'][-1]
    assert snapshot['manifest']['components'][0]['parameters']=={'height':40}
    assert result['geometryExecuted'] is False


def test_optional_get_and_dependency_reads_are_not_missing_parameter_claims():
    result=workspace_status({'manifest':{'components':[{'id':'a','source':'parts/a.py','parameters':{}}],
        'rootComponentId':'a'},'files':{'parts/a.py':
        'def build(p, d):\n    return p.get("width", 10) + d["height"]'}})
    assert result['sourceDiagnostics'][0]['referencedMissingParameters']==[]
    assert result['stagingProblems']==[]
