"""Replay completed node receipts and commit source changes with the node outcome."""

from copy import deepcopy

from langchain_core.runnables import RunnableConfig

from .. import db
from ..execution import digest
from ..services.runs import lease_context, candidate_transaction
from ..services.research_execution import fenced_operation


def durable_node(name, node):
    async def execute(state, config: RunnableConfig):
        if state.get("harness_version", 3) < 4:
            return await node(state)
        lease = lease_context.get()
        if not lease or lease["run_id"] != state["run_id"]:
            raise ValueError("Durable node requires its claimed worker lease")
        step = config.get("metadata", {}).get("langgraph_step")
        if not isinstance(step, int):
            raise ValueError("Durable node is missing its persisted graph step identity")
        key = f"node:{name}:{step}"
        input_hash = digest(state)
        fence = {
            "p_run": state["run_id"],
            "p_worker": lease["worker"],
            "p_generation": lease["generation"],
        }
        previous = await db.rpc(
            "begin_research_job",
            {**fence, "p_key": key, "p_kind": name, "p_hash": input_hash, "p_input": state},
        )
        if previous["status"] == "complete":
            return previous["result"]
        pending = {
            "run_id": state["run_id"],
            "snapshot": None,
            "hash": None,
            "expected_version": lease["candidate_version"],
        }
        token = candidate_transaction.set(pending)
        try:
            async with fenced_operation():
                from .structured import StructuredRetry

                try:
                    result = await node(deepcopy(state))
                except StructuredRetry as retry:
                    result = {**retry.update, "phase": name}
                version = await db.rpc(
                    "commit_research_job",
                    {
                        **fence,
                        "p_key": key,
                        "p_hash": input_hash,
                        "p_result": result,
                        "p_expected_version": pending["expected_version"],
                        "p_candidate_hash": pending["hash"],
                        "p_snapshot": pending["snapshot"],
                    },
                )
                lease["candidate_version"] = version
            return result
        finally:
            candidate_transaction.reset(token)

    return execute
