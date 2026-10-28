"""Unit tests must never call a real model or production service."""

import httpx
import pytest


@pytest.fixture(autouse=True)
def prevent_unmocked_network(monkeypatch):
    async def blocked(*args, **kwargs):
        raise AssertionError("Offline test attempted a real HTTP request; mock its transport")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
