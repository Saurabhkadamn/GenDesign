import base64
from dataclasses import replace
import json

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException, Request, Response
import pytest

from forma_api import security, tracing
from forma_api.config import settings


def test_role_bound_encryption_and_http_only_cookies(monkeypatch):
    monkeypatch.setattr(security, "settings", lambda: replace(settings(), encryption_key=base64.b64encode(bytes(range(32))).decode(), secure_cookies=True))
    encrypted = security.encrypt_secret("private-model-key", "coordinator")
    assert security.decrypt_secret(encrypted, "coordinator") == "private-model-key"
    with pytest.raises(InvalidTag):
        security.decrypt_secret(encrypted, "cad")
    response = Response()
    security.set_session(response, {"access_token": "access", "refresh_token": "refresh"})
    assert all("HttpOnly" in cookie and "Secure" in cookie and "Path=/api" in cookie for cookie in response.headers.getlist("set-cookie"))


@pytest.mark.parametrize("origin", [None, "https://attacker.example", "http://localhost:3000.evil.example"])
def test_mutations_reject_missing_or_wrong_origin(origin):
    headers = [(b"origin", origin.encode())] if origin else []
    with pytest.raises(HTTPException) as error:
        security.same_origin(Request({"type": "http", "headers": headers}))
    assert error.value.status_code == 403


def test_trace_credentials_and_signed_urls_are_removed():
    data = {"inputs": {"headers": {"Authorization": "Bearer topsecret", "Cookie": "session=secret"}, "apiKey": "private"},
            "source": "print('sk-or-v1-private0123456789')", "result": "Download https://example.com/file?token=download-secret", "other": "known-secret"}
    safe = json.dumps(tracing.sanitize(data, ["known-secret"]))
    for secret in ["topsecret", "session=secret", "private0123456789", "download-secret", "known-secret"]:
        assert secret not in safe


def test_remote_tracing_requires_tls(monkeypatch):
    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "testing-key")
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "http://remote.example")
    with pytest.raises(ValueError, match="HTTPS"):
        tracing.config()
    monkeypatch.setenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com")
    endpoint, key, project = tracing.config()
    assert endpoint == "https://api.smith.langchain.com"
    assert key == "testing-key"


def test_custom_paid_model_can_keep_saved_key_without_exposing_it(monkeypatch):
    from fastapi.testclient import TestClient
    from forma_api import api
    from forma_api.main import app
    saved = []
    async def profile(*args, **kwargs): return {"id": "admin", "role": "admin"}
    async def one(*args, **kwargs): return {"encrypted_key": "existing-encrypted-key", "key_hint": "masked", "version": 7}
    async def insert(table, row, **kwargs):
        saved.append(row)
        return [row]
    monkeypatch.setattr(api, "require_profile", profile)
    monkeypatch.setattr(api.db, "one", one)
    monkeypatch.setattr(api.db, "insert", insert)
    with TestClient(app) as client:
        response = client.post("/api/admin/models", headers={"Origin": "http://localhost:3000"},
            json={"role": "cad", "modelId": "provider/arbitrary-paid-model", "apiKey": ""})
    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert saved[0]["model_id"] == "provider/arbitrary-paid-model"
    assert saved[0]["encrypted_key"] == "existing-encrypted-key"
    assert saved[0]["active"] is False and saved[0]["tested_at"] is None
