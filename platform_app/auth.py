"""SQLite-backed sessions and the API authorization boundary."""
from __future__ import annotations

import hashlib
import os
import secrets
import threading
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import HTTPException, Request
from starlette.responses import JSONResponse

COOKIE_NAME = "audit_session"
PASSWORDS = PasswordHasher()
_PASSWORD_SLOTS = threading.BoundedSemaphore(2)
_DUMMY_HASH = PASSWORDS.hash(secrets.token_urlsafe(24))


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def hash_password(password: str) -> str:
    if not 12 <= len(password) <= 256:
        raise ValueError("密码长度须为12至256个字符")
    with _PASSWORD_SLOTS:
        return PASSWORDS.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        with _PASSWORD_SLOTS:
            return PASSWORDS.verify(encoded or _DUMMY_HASH, password)
    except (VerificationError, InvalidHashError):
        return False


def public_user(user: dict) -> dict:
    return {key: user[key] for key in ("id", "username", "role")}


def current_user(request: Request) -> dict:
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(401, "请先登录")
    return user


def user_upload_root(user: dict, upload_root: Path) -> Path:
    # Check before resolve: a redirected user namespace must never become trusted.
    root = upload_root.resolve()
    candidate = root / str(user["id"])
    if candidate.is_symlink() or not candidate.resolve().is_relative_to(root):
        raise HTTPException(403, "材料目录不可用")
    candidate.mkdir(parents=True, exist_ok=True)
    return candidate.resolve()


def checked_source(source: str, user: dict, upload_root: Path) -> Path:
    root = user_upload_root(user, upload_root)
    path = Path(source).expanduser()
    if not path.is_absolute():
        path = root / path
    if not path.resolve().is_relative_to(root):
        raise HTTPException(403, "只能访问自己的材料目录")
    if not path.is_dir():
        raise HTTPException(404, "材料目录不存在")
    # Parsers recursively open child files. Reject links to other namespaces too.
    for child in path.rglob("*"):
        if not child.resolve().is_relative_to(root):
            raise HTTPException(403, "材料中存在越界链接")
    return path.resolve()


class AuthMiddleware:
    def __init__(self, app, database):
        self.app = app
        self.database = database

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        path = scope["path"]
        if not (path == "/api" or path.startswith("/api/")):
            return await self.app(scope, receive, send)
        request = Request(scope)
        database = self.database()
        error = None
        session = None
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            allowed = os.environ.get("AUDIT_PUBLIC_ORIGIN", "").rstrip("/")
            own = f"{request.url.scheme}://{request.headers.get('host', '')}"
            if origin and origin.rstrip("/") != (allowed or own):
                error = (403, "请求来源不受信任")
            if request.headers.get("sec-fetch-site") == "cross-site":
                error = (403, "请求来源不受信任")
        if path not in {"/api/health", "/api/auth/login"}:
            session = database.get_session(token_hash(request.cookies.get(COOKIE_NAME, "")))
            if not session:
                error = (401, "登录已过期，请重新登录")
            else:
                scope.setdefault("state", {})["user"] = session
                scope["state"]["session"] = session
                if request.method not in {"GET", "HEAD", "OPTIONS"} and not secrets.compare_digest(
                    request.headers.get("x-csrf-token", "").encode(), str(session["csrf_token"]).encode()
                ):
                    error = (403, "请求校验失败，请刷新页面")
                if path.startswith("/api/model-config") and session["role"] != "admin":
                    error = (403, "仅管理员可配置模型")
                parts = path.strip("/").split("/")
                if len(parts) >= 3 and parts[1] == "jobs":
                    job = database.get_job(parts[2])
                    if not job or job.get("owner_user_id") != session["id"]:
                        error = (404, "任务不存在")
                if len(parts) >= 4 and parts[1] == "systems":
                    system = database.get_system(parts[2])
                    if not system or system.get("owner_user_id") != session["id"]:
                        # upload/finish is not a system resource.
                        if parts[2:] != ["upload", "finish"]:
                            error = (404, "系统不存在")
        if error:
            return await JSONResponse({"detail": error[1]}, status_code=error[0],
                                      headers={"Cache-Control": "no-store"})(scope, receive, send)

        async def private_send(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"no-store"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, private_send)
