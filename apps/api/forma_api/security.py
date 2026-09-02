"""Credential envelopes compatible with the previous server; cookie sessions."""
import base64
import os
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request, Response

from . import db
from .config import settings

ACCESS = "forma_access"
REFRESH = "forma_refresh"


def encrypt_secret(value: str, role: str) -> str:
    nonce = os.urandom(12)
    key = base64.b64decode(settings().encryption_key, validate=True)
    encoded = AESGCM(key).encrypt(nonce, value.encode(), f"forma:model:{role}:v1".encode())
    return ".".join(["v1", *(base64.b64encode(x).decode() for x in (nonce, encoded[-16:], encoded[:-16]))])


def decrypt_secret(value: str, role: str) -> str:
    version, nonce, tag, ciphertext = value.split(".")
    if version != "v1":
        raise ValueError("Unsupported credential envelope")
    key = base64.b64decode(settings().encryption_key, validate=True)
    decode = lambda v: base64.b64decode(v, validate=True)
    return AESGCM(key).decrypt(decode(nonce), decode(ciphertext) + decode(tag), f"forma:model:{role}:v1".encode()).decode()


def set_session(response: Response, session: dict):
    cfg = settings()
    for name, value, age in [(ACCESS, session["access_token"], int(session.get("expires_in", 3600))),
                             (REFRESH, session["refresh_token"], 30 * 86400)]:
        response.set_cookie(name, value, httponly=True, secure=cfg.secure_cookies,
                            samesite="lax", max_age=age, path="/api")


def clear_session(response: Response):
    for name in (ACCESS, REFRESH):
        response.delete_cookie(name, path="/api", secure=settings().secure_cookies, httponly=True, samesite="lax")


def same_origin(request: Request):
    origin = request.headers.get("origin", "").rstrip("/")
    # Explicit origins plus Vercel's verified deployment URL; do not trust forwarded host.
    allowed = set(settings().origins)
    if os.getenv("VERCEL_URL"):
        allowed.add(f"https://{os.environ['VERCEL_URL']}")
    if not origin or origin not in allowed:
        raise HTTPException(403, "Cross-origin request rejected.")


async def require_profile(request: Request, response: Response, *, admin=False, allow_password=False):
    token = request.cookies.get(ACCESS)
    try:
        if not token:
            raise HTTPException(401)
        user = await db.auth("user", token=token)
    except HTTPException as exc:
        refresh = request.cookies.get(REFRESH)
        if exc.status_code != 401 or not refresh:
            raise HTTPException(401, "Sign in to continue.") from None
        session = await db.auth("token?grant_type=refresh_token", method="POST", body={"refresh_token": refresh})
        set_session(response, session)
        token = session["access_token"]
        user = session["user"]
    profile = await db.one("profiles", {"id": f"eq.{user['id']}"}, required=False)
    if not profile or not profile["active"]:
        raise HTTPException(403, "This account is not active. Contact your administrator.")
    if profile["must_change_password"] and not allow_password:
        raise HTTPException(403, "Change your temporary password before continuing.")
    if admin and profile["role"] != "admin":
        raise HTTPException(403, "Administrator access required.")
    request.state.access_token = token
    return profile
