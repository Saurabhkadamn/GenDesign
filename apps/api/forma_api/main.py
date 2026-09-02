"""HTTP entrypoint. Workflow routes are compiled by the Vercel Python runtime."""
from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from . import db
from .api import router
from .config import settings
from .models import ModelFailure


@asynccontextmanager
async def lifespan(app):
    yield
    await db.close_client()


app = FastAPI(title="Forma API", version="0.3.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json")


@app.middleware("http")
async def private_responses(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@app.exception_handler(HTTPException)
async def http_error(request, error):
    return JSONResponse({"error": error.detail}, status_code=error.status_code, headers={"Cache-Control": "no-store"})


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValidationError)
async def validation_error(request, error):
    # Pydantic's default includes rejected inputs, which can contain credentials.
    return JSONResponse({"error": "The request contains invalid or missing fields."}, status_code=400)


@app.exception_handler(ModelFailure)
async def model_error(request, error):
    return JSONResponse({"error": str(error), "category": error.category}, status_code=400)


@app.exception_handler(Exception)
async def unexpected_error(request, error):
    logging.getLogger("forma").error("request_failed", extra={"error_type": type(error).__name__})
    return JSONResponse({"error": "The service could not complete this request. Your saved design is unchanged."}, status_code=500)


@app.get("/api/health")
async def health():
    return {"backend": "python", "version": "0.3.0", "configured": settings().configured,
        "graphConfigured": bool(os.getenv("SUPABASE_DATABASE_URL"))}


# Register last so the compatibility catch-all does not shadow health.
app.include_router(router)

_openapi = app.openapi


def contract_openapi():
    from .contracts import FrontendContracts
    schema = _openapi()
    definitions = FrontendContracts.model_json_schema(mode="serialization", ref_template="#/components/schemas/{model}")
    schema.setdefault("components", {}).setdefault("schemas", {}).update(definitions.pop("$defs", {}))
    schema["components"]["schemas"]["FrontendContracts"] = definitions
    return schema


app.openapi = contract_openapi
