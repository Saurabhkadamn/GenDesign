from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from forma_api import db


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def response(status_code, body=None):
    return httpx.Response(status_code, json=body or {"ok": True})


def configure(monkeypatch, fake_client):
    monkeypatch.setattr(db, "client", lambda: fake_client)
    monkeypatch.setattr(db, "settings", lambda: SimpleNamespace(
        supabase_url="https://example.supabase.co"))
    monkeypatch.setattr(db, "headers", lambda: {"apikey": "test"})


async def record_delay(delays, delay):
    delays.append(delay)


@pytest.mark.asyncio
async def test_retries_idempotent_artifact_upload_after_520(monkeypatch):
    fake = FakeClient([response(520, {"message": "upstream detail"}),
                       response(200, {"Key": "stored"})])
    delays = []
    configure(monkeypatch, fake)
    monkeypatch.setattr(db.asyncio, "sleep", lambda delay: record_delay(delays, delay))

    result = await db.storage("object/cad-private/run/candidate.glb", "POST",
                              content=b"candidate-bytes", content_type="model/gltf-binary")

    assert result == {"Key": "stored"}
    assert len(fake.calls) == 2
    assert delays == [0.25]
    assert all(call[2]["content"] == b"candidate-bytes" for call in fake.calls)
    assert all(call[2]["headers"]["x-upsert"] == "true" for call in fake.calls)


@pytest.mark.asyncio
async def test_exhausted_storage_retries_surface_safe_upstream_status(monkeypatch):
    fake = FakeClient([response(520, {"message": "private provider details"}) for _ in range(3)])
    delays = []
    configure(monkeypatch, fake)
    monkeypatch.setattr(db.asyncio, "sleep", lambda delay: record_delay(delays, delay))

    with pytest.raises(HTTPException) as raised:
        await db.storage("object/cad-private/run/candidate.glb", "POST",
                         content=b"candidate-bytes", content_type="model/gltf-binary")

    assert raised.value.status_code == 503
    assert "HTTP 520" in raised.value.detail
    assert "private provider details" not in raised.value.detail
    assert len(fake.calls) == 3
    assert delays == [0.25, 0.75]


@pytest.mark.asyncio
async def test_nontransient_or_nonupload_storage_requests_are_not_retried(monkeypatch):
    bad_upload = FakeClient([response(403, {"message": "sensitive"}), response(200)])
    configure(monkeypatch, bad_upload)

    with pytest.raises(HTTPException, match="403"):
        await db.storage("object/cad-private/run/candidate.glb", "POST", content=b"bytes")
    assert len(bad_upload.calls) == 1

    failed_signing = FakeClient([response(520), response(200)])
    configure(monkeypatch, failed_signing)
    with pytest.raises(HTTPException):
        await db.storage("object/sign/cad-private/run/file.glb", "POST", body={"expiresIn": 120})
    assert len(failed_signing.calls) == 1


@pytest.mark.asyncio
async def test_retries_idempotent_upload_after_transport_error(monkeypatch):
    fake = FakeClient([httpx.ConnectError("connection reset"),
                       response(200, {"Key": "stored"})])
    delays = []
    configure(monkeypatch, fake)
    monkeypatch.setattr(db.asyncio, "sleep", lambda delay: record_delay(delays, delay))

    result = await db.storage("object/cad-private/run/candidate.step", "POST",
                              content=b"candidate-bytes", content_type="application/step")

    assert result == {"Key": "stored"}
    assert len(fake.calls) == 2
    assert delays == [0.25]