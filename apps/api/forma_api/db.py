"""Small asynchronous Supabase REST adapter with no raw errors in public responses."""
from typing import Any
from urllib.parse import quote

import httpx
from fastapi import HTTPException

from .config import settings

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=20, follow_redirects=False)
    return _client


async def close_client():
    if _client is not None:
        await _client.aclose()


def headers(token: str | None = None) -> dict[str, str]:
    cfg = settings()
    # Supabase secret keys use apikey; user JWTs additionally use Authorization.
    return {"apikey": cfg.supabase_key, **({"Authorization": f"Bearer {token}"} if token else {})}


async def rest(table: str, method="GET", *, params=None, body=None, prefer=None) -> Any:
    cfg = settings()
    if not cfg.configured:
        raise HTTPException(503, "Connect Supabase to enable your workspace.")
    response = await client().request(method, f"{cfg.supabase_url}/rest/v1/{table}", params=params, json=body,
                                      headers={**headers(), "Prefer": prefer or "return=representation"})
    if not response.is_success:
        # Never include provider payloads: they may contain private source or credentials.
        code = response.json().get("message", "") if response.headers.get("content-type", "").startswith("application/json") else ""
        known = {
            "STALE_REVISION": (409, "The project changed. Refresh before sending your request."),
            "RUN_NOT_PAUSED": (409, "Only paused work can be continued."),
            "RUN_NOT_ACTIVE": (409, "This run is no longer active. Resume it before publishing."),
            "LEASE_LOST": (409, "The worker lease expired. Continue to retry this run."),
            "VALIDATION_REQUIRED": (422, "The CAD validation evidence is incomplete."),
            "ACCOUNT_INACTIVE": (403, "The account is not active for publishing."),
            "EMERGENCY_STOP": (503, "CAD publication is temporarily stopped by the administrator."),
        }
        for key, (status_code, message) in known.items():
            if key in code:
                raise HTTPException(status_code, message)
        raise HTTPException(503, "The project database could not complete this operation.")
    return response.json() if response.content else None


async def one(table: str, params: dict, *, required=True) -> dict | None:
    rows = await rest(table, params={**params, "limit": "1"})
    if not rows:
        if required:
            raise HTTPException(404, "Record not found.")
        return None
    return rows[0]


async def rpc(name: str, body: dict):
    return await rest(f"rpc/{name}", "POST", body=body)


async def update(table: str, body: dict, **filters):
    return await rest(table, "PATCH", params={k: f"eq.{v}" for k, v in filters.items()}, body=body)


async def insert(table: str, body: dict, *, conflict: str | None = None):
    return await rest(table, "POST", params={"on_conflict": conflict} if conflict else None, body=body,
                      prefer="resolution=merge-duplicates,return=representation" if conflict else None)


async def auth(path: str, *, method="GET", token=None, body=None, admin=False):
    cfg = settings()
    key = cfg.supabase_key if admin else cfg.publishable_key
    response = await client().request(method, f"{cfg.supabase_url}/auth/v1/{path}", json=body,
                                      headers={"apikey": key, **({"Authorization": f"Bearer {token}"} if token else {})})
    if not response.is_success:
        raise HTTPException(401 if not admin else 400, "Authentication could not be completed. Check your account details.")
    return response.json() if response.content else {}


async def storage(path: str, method="GET", *, content=None, content_type=None, body=None):
    response = await client().request(method, f"{settings().supabase_url}/storage/v1/{path}", json=body,
                                      content=content, headers={**headers(), "x-upsert": "true",
                                                               **({"Content-Type": content_type} if content_type else {})})
    if not response.is_success:
        raise HTTPException(503, "Private file storage is temporarily unavailable.")
    return response.json() if response.content else {}


def object_path(path: str) -> str:
    return quote(path, safe="/")
