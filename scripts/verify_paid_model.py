"""Verify and save one explicitly selected OpenRouter paid/free connection.

The script makes exactly one synthetic tool-call request for the selected model.
The API stores the key encrypted and returns only its hint.
"""
import asyncio
import json
from pathlib import Path

import httpx
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / "test-results/openrouter-testing.env")


async def main():
    config = json.loads((ROOT / "test-results/production-access.json").read_text())
    credentials = dict(line.split(":", 1) for line in (ROOT / "test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
    model = "deepseek/deepseek-v4-flash-0731"
    key = __import__("os").environ["OPENROUTER_API_KEY"]
    async with httpx.AsyncClient(base_url=config["url"], timeout=90, follow_redirects=True,
                                 limits=httpx.Limits(max_keepalive_connections=0)) as client:
        (await client.get(config["shareUrl"])).raise_for_status()
        client.headers["Origin"] = config["url"]
        login = await client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
        login.raise_for_status()
        saved = await client.post("/api/admin/models", json={"role": "coordinator", "modelId": model, "apiKey": key})
        saved.raise_for_status()
        tested = await client.post("/api/admin/models/coordinator/test", json={})
        tested.raise_for_status()
        activated = await client.post("/api/admin/models/coordinator/activate", json={})
        activated.raise_for_status()
        rows = (await client.get("/api/admin/models")).json()
        row = next(item for item in rows if item["role"] == "coordinator")
        assert row["model_id"] == model and row["active"] is True and row["tested_at"]
        result = {"model": model, "connectionTest": tested.json(), "savedKeyHint": row["key_hint"], "activation": activated.json()}
        (ROOT / "test-results/paid-model-verification.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"model": model, "connectionTest": tested.json(), "keyStored": True, "activation": activated.json()}))


if __name__ == "__main__":
    asyncio.run(main())
