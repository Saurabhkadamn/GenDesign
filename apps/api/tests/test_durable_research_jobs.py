from copy import deepcopy

import pytest

from forma_api.graphs.jobs import durable_node
from forma_api.services import runs
from forma_api import db
from forma_api.execution import digest


@pytest.mark.asyncio
async def test_checkpoint_loss_replays_node_receipt_without_executing_edit_twice(monkeypatch):
    committed, jobs, calls = {}, {}, []
    state = {"harness_version": 4, "run_id": "run", "phase": "cad_session"}
    snapshot = {"manifest": {}, "files": {"parts/part.py": "new source"}}

    async def rpc(name, value):
        if name == "check_research_fence":
            return True
        if name == "begin_research_job":
            return jobs.get(value["p_key"], {"status": "running"})
        if name == "commit_research_job":
            assert committed == {}  # source has not escaped the atomic job commit
            committed.update(deepcopy(value["p_snapshot"]))
            jobs[value["p_key"]] = {"status": "complete", "result": deepcopy(value["p_result"])}
            return 1
        pytest.fail("unexpected database mutation " + name)

    async def edit(input_state):
        calls.append(1)
        await runs.save_candidate("run", snapshot, digest(snapshot))
        assert await runs.load_candidate("run") == snapshot
        assert committed == {}
        return {"phase": "build", "candidate_hash": digest(snapshot)}

    monkeypatch.setattr(db, "rpc", rpc)
    lease = runs.lease_context.set(
        {"run_id": "run", "worker": "w", "generation": 1, "candidate_version": 0}
    )
    try:
        node = durable_node("cad_session", edit)
        first = await node(state, {"metadata": {"langgraph_step": 7}})
        assert committed == snapshot
        # Simulate a crash after effects/receipt committed, before LangGraph saved its delta.
        second = await node(state, {"metadata": {"langgraph_step": 7}})
        assert first == second
        assert calls == [1]
    finally:
        runs.lease_context.reset(lease)


@pytest.mark.asyncio
async def test_failed_node_does_not_expose_uncommitted_source(monkeypatch):
    async def rpc(name, value):
        if name == "check_research_fence":
            return True
        if name == "begin_research_job":
            return {"status": "running"}
        pytest.fail("failed node attempted a commit")

    async def edit(state):
        await runs.save_candidate("run", {"files": {"part": "unsaved"}}, "hash")
        raise ValueError("operation failed")

    monkeypatch.setattr(db, "rpc", rpc)
    lease = runs.lease_context.set(
        {"run_id": "run", "worker": "w", "generation": 1, "candidate_version": 0}
    )
    try:
        with pytest.raises(ValueError, match="operation failed"):
            await durable_node("cad_session", edit)(
                {"harness_version": 4, "run_id": "run"}, {"metadata": {"langgraph_step": 3}}
            )
        assert runs.candidate_transaction.get() is None
    finally:
        runs.lease_context.reset(lease)


@pytest.mark.asyncio
async def test_real_graph_restart_after_checkpoint_write_failure_reuses_committed_edit(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.graph import StateGraph, START, END
    from forma_api.graphs.state import AgentState

    class InterruptedSaver(InMemorySaver):
        failed = False

        async def aput_writes(self, config, writes, task_id, task_path=""):
            if not self.failed and any(
                channel == "phase" and value == "done" for channel, value in writes
            ):
                self.failed = True
                raise ConnectionError("checkpoint lost after node transaction committed")
            return await super().aput_writes(config, writes, task_id, task_path)

    jobs, committed, edits = {}, [], []

    async def rpc(name, value):
        if name == "check_research_fence":
            return True
        if name == "begin_research_job":
            previous = jobs.get(value["p_key"])
            if previous:
                assert previous["input_hash"] == value["p_hash"]
                return previous
            return {"status": "running"}
        assert name == "commit_research_job"
        committed.append(value["p_snapshot"])
        jobs[value["p_key"]] = {
            "status": "complete",
            "result": deepcopy(value["p_result"]),
            "input_hash": value["p_hash"],
        }
        return 1

    async def edit(state):
        edits.append(1)
        await runs.save_candidate("run", {"files": {"parts/a.py": "new source"}}, "new-candidate")
        return {"phase": "done", "candidate_hash": "new-candidate"}

    monkeypatch.setattr(db, "rpc", rpc)
    builder = StateGraph(AgentState)
    builder.add_node("edit", durable_node("edit", edit))
    builder.add_edge(START, "edit")
    builder.add_edge("edit", END)
    saver = InterruptedSaver()
    config = {"configurable": {"thread_id": "checkpoint-crash"}}
    token = runs.lease_context.set(
        {"run_id": "run", "worker": "first", "generation": 1, "candidate_version": 0}
    )
    try:
        with pytest.raises(ConnectionError, match="checkpoint lost"):
            await builder.compile(checkpointer=saver).ainvoke(
                {"run_id": "run", "harness_version": 4, "phase": "edit"}, config
            )
        runs.lease_context.get().update(worker="successor", generation=2, candidate_version=1)
        result = await builder.compile(checkpointer=saver).ainvoke(None, config)
        assert result["candidate_hash"] == "new-candidate"
        assert len(committed) == len(edits) == 1
    finally:
        runs.lease_context.reset(token)
