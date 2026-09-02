"""Python HTTP application routes. No model inference runs inside a chat request."""
import asyncio
import json
import os
import re

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from . import db, models, repository as repo
from .config import settings
from .contracts import AppSettings, ChatRequest, ResumeRequest, SessionView, TERMINAL, Project, WorkspaceState, Run, ModelConfigView, ModelOptions
from .security import clear_session, encrypt_secret, require_profile, same_origin, set_session

router = APIRouter(prefix="/api")


async def body(request: Request, maximum=180000):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "JSON required.")
    chunks, length = [], 0
    async for chunk in request.stream():
        length += len(chunk)
        if length > maximum:
            raise HTTPException(413, "Request body is too large.")
        chunks.append(chunk)
    try:
        value = json.loads(b"".join(chunks))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Invalid JSON object.") from None


def text(data: dict, key: str, minimum=1, maximum=100):
    value = data.get(key)
    if not isinstance(value, str) or not minimum <= len(value.strip()) <= maximum:
        raise HTTPException(400, f"Invalid {key}.")
    return value if key in ("password", "apiKey") else value.strip()


@router.get("/session", response_model=SessionView)
async def session(request: Request, response: Response):
    if not settings().configured:
        return {"configured": False, "profile": None}
    try:
        profile = await require_profile(request, response, allow_password=True)
    except HTTPException as exc:
        if exc.status_code != 401:
            raise
        profile = None
    return {"configured": True, "profile": profile}


@router.get("/runs/{run_id}/events")
async def events(run_id: str, request: Request, response: Response):
    profile = await require_profile(request, response)
    run = await repo.owned_run(run_id, profile["id"])
    try:
        cursor = max(0, int(request.headers.get("last-event-id", request.query_params.get("after", "0"))))
    except ValueError:
        raise HTTPException(400, "Invalid event cursor.") from None

    async def stream():
        nonlocal cursor
        for index in range(100):
            if await request.is_disconnected():
                break
            if index % 10 == 0:
                current_profile = await db.one("profiles", {"id": f"eq.{profile['id']}"})
                if not current_profile["active"] or current_profile["must_change_password"]:
                    return
            rows = await db.rest("run_events", params={"run_id": f"eq.{run_id}", "id": f"gt.{cursor}", "order": "id.asc", "limit": 100})
            for row in rows:
                cursor = row["id"]
                yield f"id: {cursor}\nevent: progress\ndata: {json.dumps(row)}\n\n"
            current = await db.one("runs", {"id": f"eq.{run_id}", "select": "status"})
            if current["status"] in TERMINAL:
                yield f"event: terminal\ndata: {json.dumps({'status': current['status']})}\n\n"
                return
            yield ": heartbeat\n\n"
            await asyncio.sleep(1)
    # Retain any refreshed session cookies on this streaming response.
    result = StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "private, no-store", "X-Accel-Buffering": "no"})
    result.raw_headers.extend((k, v) for k, v in response.raw_headers if k == b"set-cookie")
    return result


@router.get("/projects", response_model=list[Project])
async def projects(request: Request, response: Response):
    return await dispatch("projects", request, response)


@router.get("/projects/{project_id}", response_model=WorkspaceState)
async def project_workspace(project_id: str, request: Request, response: Response):
    return await dispatch(f"projects/{project_id}", request, response)


@router.get("/runs/{run_id}", response_model=Run)
async def get_run(run_id: str, request: Request, response: Response):
    return await dispatch(f"runs/{run_id}", request, response)


@router.get("/admin/models", response_model=list[ModelConfigView])
async def get_models(request: Request, response: Response):
    return await dispatch("admin/models", request, response)


@router.get("/admin/model-options", response_model=ModelOptions)
async def get_model_options(request: Request, response: Response):
    return await dispatch("admin/model-options", request, response)


@router.api_route("/{path:path}", methods=["GET", "POST", "DELETE"], include_in_schema=False)
async def dispatch(path: str, request: Request, response: Response):
    method = request.method
    if method != "GET":
        same_origin(request)
    response.headers["Cache-Control"] = "private, no-store"
    parts = path.split("/")
    if path == "auth/login" and method == "POST":
        data = await body(request, 5000)
        result = await db.auth("token?grant_type=password", method="POST", body={"email": text(data, "email", 3, 254), "password": text(data, "password", 1, 256)})
        profile = await db.one("profiles", {"id": f"eq.{result['user']['id']}"}, required=False)
        if not profile or not profile["active"]:
            raise HTTPException(403, "This account is not active.")
        set_session(response, result)
        return {"profile": profile}
    if path == "auth/logout" and method == "POST":
        from .security import ACCESS
        token = request.cookies.get(ACCESS)
        if token:
            try:
                await db.auth("logout", method="POST", token=token)
            except HTTPException:
                pass
        clear_session(response)
        return {"ok": True}
    profile = await require_profile(request, response, admin=path.startswith("admin/"), allow_password=path == "auth/password")
    owner = profile["id"]
    if path == "auth/password" and method == "POST":
        password = text(await body(request), "password", 12, 128)
        await db.auth("user", method="PUT", token=request.state.access_token, body={"password": password})
        await db.update("profiles", {"must_change_password": False}, id=owner)
        return {"ok": True}
    if path == "projects":
        if method == "GET":
            return await db.rest("projects", params={"owner_id": f"eq.{owner}", "order": "updated_at.desc"})
        if method == "POST":
            name = text(await body(request), "name", 1, 100)
            response.status_code = 201
            return (await db.insert("projects", {"name": name, "owner_id": owner}))[0]
    if parts[0] == "projects" and len(parts) >= 2:
        project_id = repo.identifier(parts[1])
        project = await repo.owned_project(project_id, owner)
        if len(parts) == 2 and method == "GET":
            return await repo.workspace(project_id, owner)
        if len(parts) == 3 and parts[2] == "chat" and method == "POST":
            if os.getenv("VERCEL") == "1" and not os.getenv("SUPABASE_DATABASE_URL"):
                raise HTTPException(503, "LangGraph checkpoint storage is not configured. Add the server-only Supabase transaction-pooler URL.")
            payload = ChatRequest.model_validate(await body(request))
            app_settings = await db.one("app_settings", {"id": "eq.true"})
            if app_settings["settings"]["emergencyStop"]:
                raise HTTPException(423, "New work is paused by the administrator.")
            await models.configuration("coordinator")
            run_id = await db.rpc("submit_run_v3", {"p_project": project_id, "p_owner": owner,
                "p_base": str(payload.baseRevisionId) if payload.baseRevisionId else None,
                "p_message": payload.message, "p_selected": payload.selectedIds,
                "p_key": str(payload.idempotencyKey), "p_environment": settings().environment})
            from .engine import dispatch_run
            await dispatch_run(run_id)
            response.status_code = 202
            return {"runId": run_id}
        if len(parts) == 3 and parts[2] == "feedback" and method == "POST":
            data = await body(request)
            run_id = data.get("runId")
            if run_id:
                run = await repo.owned_run(run_id, owner)
                if run["project_id"] != project_id:
                    raise HTTPException(404, "Run not found.")
            await db.insert("feedback", {"project_id": project_id, "revision_id": project["current_revision_id"], "run_id": run_id, "owner_id": owner, "content": text(data, "content", 1, 4000)})
            return {"ok": True}
    if parts[0] == "runs" and len(parts) >= 2:
        run = await repo.owned_run(parts[1], owner)
        if len(parts) == 2 and method == "GET":
            return run
        if len(parts) == 3 and method == "POST":
            if run.get("backend_version") != 3 or run.get("execution_environment") != settings().environment:
                raise HTTPException(409, "This run belongs to another runtime. Start a new request here.")
            if parts[2] == "cancel":
                await db.rest("runs", "PATCH", params={"id": f"eq.{run['id']}", "status": "in.(queued,running,paused,waiting_input)"}, body={"status": "cancelled", "updated_at": repo.utcnow()})
                from .engine import cancel_run
                await cancel_run(run["id"])
                return {"ok": True}
            if parts[2] == "continue":
                from .services.runs import resume
                from .graphs.runner import dispatch_run
                await resume(run["id"], owner)
                await dispatch_run(run["id"], {"kind": "continue"})
                return {"runId": run["id"]}
            if parts[2] == "resume":
                payload = ResumeRequest.model_validate(await body(request, 13000))
                if run["status"] not in ("waiting_input", "paused"):
                    raise HTTPException(409, "This run is not waiting for input.")
                from .services.runs import resume
                from .graphs.runner import dispatch_run
                value = payload.model_dump()
                message = payload.message or ({"approval": "Approved engineering proposal.",
                    "rejection": "Rejected engineering proposal.", "continue": "Continue."}.get(payload.kind))
                if message:
                    await db.update("messages", {"content": f"{run['message']}\n\nResponse: {message}"},
                        run_id=run["id"], role="user")
                await resume(run["id"], owner)
                await dispatch_run(run["id"], value)
                return {"runId": run["id"]}
    if parts[0] == "artifacts" and len(parts) == 2 and method == "GET":
        return await repo.signed_artifact(parts[1], owner)
    if path == "admin/settings":
        if method == "GET":
            return (await db.one("app_settings", {"id": "eq.true"}))["settings"]
        if method == "POST":
            value = AppSettings.model_validate(await body(request)).model_dump()
            await db.update("app_settings", {"settings": value}, id="true")
            return {"ok": True}
    if path == "admin/model-options" and method == "GET":
        rows = await models.catalog()
        eligible = rows
        return {"freeOnly": settings().free_only, "syntheticNemotronTesting": settings().nemotron_testing,
                "models": sorted([{"id": m["id"], "name": m.get("name", m["id"]), "contextLength": m.get("context_length", 0)} for m in eligible], key=lambda x: x["name"])}
    if path == "admin/models":
        if method == "GET":
            return await db.rest("model_configs", params={"select": "role,model_id,key_hint,active,version,tested_at"})
        if method == "POST":
            data = await body(request, 5000)
            role = data.get("role")
            if role not in ("coordinator", "cad", "engineering"):
                raise HTTPException(400, "Invalid role.")
            model_id = text(data, "modelId", 3, 160)
            old = await db.one("model_configs", {"role": f"eq.{role}"}, required=False)
            if data.get("apiKey"):
                key = text(data, "apiKey", 10, 512)
                encrypted, hint = encrypt_secret(key, role), "••••" + key[-4:]
            elif old:
                encrypted, hint = old["encrypted_key"], old["key_hint"]
            else:
                raise HTTPException(400, "Enter an API key for the first connection.")
            await db.insert("model_configs", {"role": role, "model_id": model_id, "encrypted_key": encrypted, "key_hint": hint, "active": False, "tested_at": None, "version": (old or {}).get("version", 0) + 1, "updated_at": repo.utcnow()}, conflict="role")
            return {"ok": True}
    if parts[:2] == ["admin", "models"] and len(parts) >= 3:
        role = parts[2]
        if role not in ("coordinator", "cad", "engineering"):
            raise HTTPException(400, "Invalid role.")
        if len(parts) == 3 and method == "DELETE":
            await db.rest("model_configs", "DELETE", params={"role": f"eq.{role}"})
            return {"ok": True}
        if len(parts) == 4 and method == "POST":
            if parts[3] == "test":
                return await models.test_connection(role)
            if parts[3] == "activate":
                row = await db.one("model_configs", {"role": f"eq.{role}"})
                if not row["tested_at"]:
                    raise HTTPException(409, "Test this configuration before activating it.")
                updated = await db.update("model_configs", {"active": True}, role=role, version=row["version"])
                if not updated:
                    raise HTTPException(409, "The configuration changed. Test the current version.")
                return {"ok": True}
    if path == "admin/users":
        if method == "GET":
            return await db.rest("profiles", params={"order": "created_at.desc"})
        if method == "POST":
            data = await body(request, 5000)
            email, name, password = text(data, "email", 3, 254), text(data, "name", 1, 80), text(data, "password", 12, 128)
            result = await db.auth("admin/users", method="POST", admin=True, body={"email": email, "password": password, "email_confirm": True})
            user_id = result.get("id", (result.get("user") or {}).get("id"))
            try:
                await db.insert("profiles", {"id": user_id, "email": email, "display_name": name, "must_change_password": True})
            except Exception:
                await db.auth(f"admin/users/{user_id}", method="DELETE", admin=True)
                raise
            response.status_code = 201
            return {"ok": True}
    if parts[:2] == ["admin", "users"] and len(parts) == 3 and method == "POST":
        user_id = repo.identifier(parts[2])
        if user_id == owner:
            raise HTTPException(400, "Use your account settings to change your own password.")
        data = await body(request, 5000)
        if "password" in data:
            await db.auth(f"admin/users/{user_id}", method="PUT", admin=True, body={"password": text(data, "password", 12, 128)})
            await db.update("profiles", {"must_change_password": True}, id=user_id)
        if "active" in data:
            if not isinstance(data["active"], bool):
                raise HTTPException(400, "Invalid account status.")
            await db.update("profiles", {"active": data["active"]}, id=user_id)
        return {"ok": True}
    if path == "admin/tracing" and method == "GET":
        from .tracing import status
        return await status()
    if path == "admin/tracing/test" and method == "POST":
        from .tracing import connection_test
        return await connection_test()
    if path == "admin/checkpoints/setup" and method == "POST":
        from .services.checkpoints import setup_and_harden
        await setup_and_harden()
        return {"ok": True}
    if parts[0] == "runs" and len(parts) == 3 and parts[2] == "trace" and method == "GET":
        run = await repo.owned_run(parts[1], owner)
        if profile["role"] != "admin":
            raise HTTPException(403, "Administrator access required for private traces.")
        from .tracing import trace_link
        return await trace_link(run["id"])
    raise HTTPException(404, "Endpoint not found.")
