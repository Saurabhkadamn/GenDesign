import pytest

from forma_api.repositories import projects


@pytest.mark.asyncio
async def test_context_keeps_first_brief_latest_turns_and_revision_evidence(monkeypatch):
    async def owned_project(project_id, owner_id):
        assert owner_id == "owner"
        return {"id": project_id, "name": "Bearing"}

    async def rest(table, *, params):
        if table == "revisions":
            return [{"id": "r1", "ordinal": 1, "summary": "First draft"}]
        if table in ("runs", "calculations"):
            return []
        if params.get("role") == "eq.user":
            return [{"run_id": "old", "role": "user", "content": "Original bearing design brief"}]
        return [
            {"run_id": "current", "role": "user", "content": "Change the groove"},
            {"run_id": "prior", "role": "assistant", "content": "The draft has unverified checks."},
            {"run_id": "prior", "role": "user", "content": "Use eight balls."},
        ]

    async def one(table, params, *, required=True):
        assert table == "revisions"
        return {"id": "r1", "summary": "First draft", "manifest": {"components": []},
            "validation": {"requirements": [{"status": "unverified"}],
                "allRequirementsVerified": False,
                "components": {"plate": {"dimensions": [8, 8, 4], "solids": 1, "valid": True}},
                "inspection": {"components": {"plate": {"boundsMm": [-4, -4, -2.001, 4, 4, 2.001]}}}}}

    monkeypatch.setattr(projects, "owned_project", owned_project)
    monkeypatch.setattr(projects.db, "rest", rest)
    monkeypatch.setattr(projects.db, "one", one)
    result = await projects.agent_context("project", "owner", "current", "r1", ["outer_race"])
    assert result["previousMessages"][0]["content"] == "Original bearing design brief"
    assert all(message["content"] != "Change the groove" for message in result["previousMessages"])
    assert result["revision"]["allRequirementsVerified"] is False
    assert result["selectedIds"] == ["outer_race"]
    # Accepted STEP dimensions must be available when the next turn changes
    # only thickness; a preview-inflated inspection box is a different fact.
    assert result["revision"]["componentMeasurements"]["plate"]["dimensions"] == [8, 8, 4]


@pytest.mark.asyncio
async def test_context_redacts_credentials_from_old_messages(monkeypatch):
    async def owned_project(_project_id, _owner_id):
        return {"name": "Test"}

    async def rest(table, *, params):
        if table in ("revisions", "runs", "calculations"):
            return []
        return [{"run_id": "old", "role": "user",
            "content": "my key is nvapi-exampleprivatevalue123456"}]

    monkeypatch.setattr(projects, "owned_project", owned_project)
    monkeypatch.setattr(projects.db, "rest", rest)
    result = await projects.agent_context("project", "owner", "current", None, [])
    assert "nvapi-exampleprivatevalue123456" not in str(result)
