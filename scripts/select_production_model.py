"""Test and activate the selected provider and model for all CAD roles."""
import json
import httpx
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
provider = os.getenv("FORMA_PROVIDER", "openai_compatible")
load_dotenv(ROOT / ("test-results/gateway-testing.env" if provider == "openai_compatible" else "test-results/openrouter-testing.env"))
base = os.getenv("FORMA_ACCEPTANCE_URL", "https://forma-cad-eosin.vercel.app").rstrip("/")
model = os.getenv("FORMA_MODEL", "minimax/minimax-m3" if provider == "openai_compatible" else "minimax/minimax-m3:free")
provider_url = os.getenv("FORMA_PROVIDER_URL", "https://ai-gateway.vercel.sh/v1") if provider == "openai_compatible" else None
key = os.environ["AI_GATEWAY_API_KEY" if provider == "openai_compatible" else "OPENROUTER_API_KEY"]
credentials = dict(line.split(":", 1) for line in Path("test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
evidence = {"deployment": base, "provider": provider, "baseUrl": provider_url, "model": model, "roles": []}
with httpx.Client(base_url=base, timeout=260, follow_redirects=True) as client:
    client.headers["Origin"] = base
    login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
    login.raise_for_status()
    for role in ("coordinator", "cad", "engineering"):
        response = client.post("/api/admin/models", json={"role": role, "provider": provider,
            "baseUrl": provider_url, "modelId": model, "apiKey": key})
        response.raise_for_status()
        test = client.post(f"/api/admin/models/{role}/test")
        print("test", role, test.status_code, test.text, flush=True)
        test.raise_for_status()
        activate = client.post(f"/api/admin/models/{role}/activate")
        activate.raise_for_status()
        evidence["roles"].append({"role": role, "tested": test.json(), "active": True})
    response = client.get("/api/admin/models")
    response.raise_for_status()
    rows = response.json()
    assert all(any(r["role"] == role and r["provider"] == provider and r["base_url"] == provider_url and
        r["model_id"] == model and r["active"] and r["tested_at"] for r in rows)
        for role in ("coordinator", "cad", "engineering"))
    (ROOT / "test-results/model-selection-verification.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence), flush=True)
