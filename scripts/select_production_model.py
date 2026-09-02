"""Select and activate a model through the authenticated production admin API."""
import httpx
import os
from pathlib import Path

base = "https://forma-cad-eosin.vercel.app"
model = os.getenv("FORMA_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free")
credentials = dict(line.split(":", 1) for line in Path("test-results/forma-admin-credentials.txt").read_text().splitlines() if ":" in line)
with httpx.Client(base_url=base, timeout=60, follow_redirects=True) as client:
    client.headers["Origin"] = base
    login = client.post("/api/auth/login", json={"email": credentials["Email"].strip(), "password": credentials["Password"].strip()})
    login.raise_for_status()
    response = client.post("/api/admin/models", json={"role": "coordinator", "modelId": model})
    response.raise_for_status()
    test = client.post("/api/admin/models/coordinator/test")
    print("test", test.status_code, test.text)
    test.raise_for_status()
    activate = client.post("/api/admin/models/coordinator/activate")
    activate.raise_for_status()
    print({"model": model, "tested": test.json(), "activated": activate.json()})
