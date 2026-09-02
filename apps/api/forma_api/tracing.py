"""Sanitized, best-effort LangSmith tracing."""
import asyncio
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid4, uuid5

from langsmith import Client

from .config import settings

SECRET_KEY = re.compile(r"authorization|cookie|password|api.?key|encrypted.?key|access.?token|refresh.?token|secret|signed.?url", re.I)
TOKEN = re.compile(r"(?:sk-or-v1-|lsv2_[A-Za-z0-9_]*|sb_secret_|sb_publishable_)[A-Za-z0-9_-]+|Bearer\s+[^\s\"']+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", re.I)
SIGNED = re.compile(r"https?://[^\s\"'<>]+(?:[?&](?:token|signature|x-amz-signature|key)=[^\s\"'<>]*)", re.I)


def sanitize(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[REDACTED]" if SECRET_KEY.search(k) else sanitize(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(v, secrets) for v in value]
    if isinstance(value, str):
        value = TOKEN.sub("[REDACTED]", SIGNED.sub("[SIGNED URL REMOVED]", value))
        for secret in secrets:
            if secret and len(secret) >= 6:
                value = value.replace(secret, "[REDACTED]")
        return value[:250_000]
    return value


def config():
    enabled = os.getenv("LANGSMITH_TRACING", "").lower() == "true"
    endpoint = os.getenv("LANGSMITH_ENDPOINT", "https://api.smith.langchain.com").rstrip("/")
    key = os.getenv("LANGSMITH_API_KEY", "")
    project = os.getenv("LANGSMITH_PROJECT", "default")
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Use an HTTPS LangSmith endpoint without credentials in its URL.")
    return (endpoint, key, project) if enabled and key else None


def _client():
    value = config()
    return Client(api_url=value[0], api_key=value[1]) if value else None


async def status():
    try:
        value = config()
        error = None
    except ValueError as exc:
        value, error = None, str(exc)
    return {"configured": value is not None, "project": os.getenv("LANGSMITH_PROJECT", "default"),
        "content": "full", "available": False, "error": error, "provider": "LangSmith"}


async def connection_test():
    client = _client()
    if not client:
        return {"configured": False, "available": False, "message": "LangSmith tracing is not configured."}
    try:
        project = os.getenv("LANGSMITH_PROJECT", "default")
        synthetic = uuid4()
        await asyncio.to_thread(client.create_run, name="Forma connection check",
            inputs={"synthetic": True}, run_type="tool", id=synthetic,
            project_name=project, outputs={"connected": True},
            end_time=datetime.now(timezone.utc))
        await asyncio.to_thread(client.flush)
        return {"configured": True, "available": True, "project": project,
            "message": "LangSmith connection authenticated and a synthetic trace was accepted.",
            "url": "https://smith.langchain.com"}
    except Exception:
        return {"configured": True, "available": False,
            "message": "LangSmith connection failed. Check the endpoint, key, project, and workspace access."}


async def record(run, key, name, started_ns, *, inputs=None, outputs=None, attributes=None, error=False):
    client = _client()
    if not client:
        return
    cfg = settings()
    secrets = [cfg.supabase_key, cfg.encryption_key, os.getenv("LANGSMITH_API_KEY"), os.getenv("FORMA_SMOKE_TOKEN")]
    metadata = sanitize({"project_id": run["project_id"], "forma_run_id": run["id"],
        "deployment": os.getenv("VERCEL_DEPLOYMENT_ID", "development"),
        "execution_environment": cfg.environment, **(attributes or {})}, secrets)
    try:
        await asyncio.wait_for(asyncio.to_thread(client.create_run, name=name,
            inputs=sanitize(inputs or {}, secrets), run_type="llm" if metadata.get("span.type") == "LLM" else "tool",
            id=uuid5(NAMESPACE_URL, f"forma:{run['id']}:{key}"),
            project_name=os.getenv("LANGSMITH_PROJECT", "default"),
            start_time=datetime.fromtimestamp(started_ns / 1e9, timezone.utc),
            end_time=datetime.now(timezone.utc), outputs=sanitize(outputs or {}, secrets),
            error="operation failed" if error else None, extra={"metadata": metadata}), timeout=3)
    except Exception:
        pass


async def trace_link(run_id):
    return {**(await connection_test()), "runId": run_id}
