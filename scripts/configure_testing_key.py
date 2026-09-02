"""Install an explicitly supplied testing key after one zero-price protocol check."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import os
import sys

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/hosted-runtime.env")
load_dotenv(ROOT / "test-results/openrouter-testing.env", override=True)
os.environ["OPENROUTER_FREE_ONLY"] = "true"
os.environ["OPENROUTER_NEMOTRON_TESTING"] = "true"
sys.path.insert(0, str(ROOT / "apps/api"))
from forma_api import db, models
from forma_api.security import encrypt_secret


async def main():
    key = os.environ["OPENROUTER_API_KEY"]
    tool = [{"type": "function", "function": {"name": "connection_check", "description": "Check tool calling",
             "parameters": {"type": "object", "properties": {"value": {"type": "string", "enum": ["ready"]}},
                            "required": ["value"], "additionalProperties": False}}}]
    try:
        result = await models.turn({"api_key": key, "model_id": models.NEMOTRON},
            [{"role": "user", "content": "Call connection_check with value ready. This is a synthetic connection test."}], tool, max_tokens=2048)
        if not any(c["name"] == "connection_check" and c["input"] == {"value": "ready"} for c in result["calls"]):
            raise models.ModelFailure("tool_protocol", "The model did not return the required test tool call.")
        for row in await db.rest("model_configs"):
            if row["model_id"] != models.NEMOTRON:
                continue  # Do not overwrite another selected model.
            updated = await db.update("model_configs", {"encrypted_key": encrypt_secret(key, row["role"]),
                "key_hint": "••••" + key[-4:], "version": row["version"] + 1,
                "tested_at": datetime.now(timezone.utc).isoformat(), "active": True}, role=row["role"], version=row["version"])
            print(row["role"], "testing key saved and verified" if updated else "configuration changed; left untouched")
    except models.ModelFailure as exc:
        print("Connection test:", exc.category, str(exc))
        raise SystemExit(1)
    finally:
        await db.close_client()


asyncio.run(main())
