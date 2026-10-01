"""Measure a typed MiniMax Gateway design-routing call without running CAD."""
import asyncio
import json
import os
from pathlib import Path
import sys
import time

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/gateway-testing.env")
sys.path.insert(0, str(ROOT / "apps/api"))
from forma_api import db
from forma_api.graphs.design import Triage
from forma_api.providers import openai_compatible
from forma_api.tools import portable_schema


async def main():
    original_chat = openai_compatible._chat

    async def capture(**kwargs):
        raw = await original_chat(**kwargs)
        (ROOT / "test-results/gateway-probe-response.json").write_text(json.dumps(raw), encoding="utf-8")
        return raw

    openai_compatible._chat = capture
    config = {"provider": "openai_compatible", "base_url": "https://ai-gateway.vercel.sh/v1",
        "model_id": "minimax/minimax-m3", "api_key": os.environ["AI_GATEWAY_API_KEY"],
        "max_output_tokens": 4096, "stream": True}
    tool = {"type": "function", "function": {"name": "submit_triage", "description": "Route the design task.",
        "parameters": portable_schema(Triage.model_json_schema())}}
    started = time.monotonic()
    try:
        result = await openai_compatible.turn(config, [
            {"role": "system", "content": "Classify the request with submit_triage. Preserve explicit requirements, marking unsupported checks unverified. Do not solve or design the part during triage."},
            {"role": "user", "content": (ROOT / "fixtures/stamped-mounting-bracket.txt").read_text()}], [tool])
        calls = result["calls"]
        validated = Triage.model_validate(next(c["input"] for c in calls if c["name"] == "submit_triage"))
        evidence = {"model": config["model_id"], "seconds": round(time.monotonic()-started, 2),
            "route": validated.route, "requirements": len(validated.requirements),
            "inputTokens": result["inputTokens"], "outputTokens": result["outputTokens"]}
        (ROOT / "test-results/gateway-design-probe.json").write_text(json.dumps(evidence, indent=2))
        print(json.dumps(evidence), flush=True)
    finally:
        await db.close_client()


if __name__ == "__main__":
    asyncio.run(main())
