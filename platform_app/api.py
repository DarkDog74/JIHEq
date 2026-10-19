from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path, PurePosixPath

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audit_service import normalize_audit_summary, paired_item_ids
from .config import PROJECT_ROOT, UPLOAD_ROOT, ai_config_status
from .database import Database
from .events import event_path
from .job_manager import JobManager
from .model_config import (
    DEFAULT_MODEL,
    load_model_config,
    probe_model_connection,
    public_view,
    save_model_config,
)
from .report import generate_report
from .auth import (AuthMiddleware, COOKIE_NAME, checked_source, current_user,
                   public_user, token_hash, user_upload_root, verify_password)


class SourceRequest(BaseModel):
    source_path: str


class ModelConfigRequest(BaseModel):
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 120
    # 请求形态：默认为 None 表示「本次没传」，交给 normalize_config 填默认值，
    # 这样只传上面 4 个字段的旧客户端行为不变。
    # 注意空字符串是有意义的值——auth_scheme="" 表示认证头里只放密钥本身，
    # chat_path="" 表示接入地址本身就是完整端点，两者都不能当缺省处理。
    auth_header: str | None = None
    auth_scheme: str | None = None
    chat_path: str | None = None
    stream: bool | None = None
    # 思考参数与最大输出 token。推理模型的思考也计入 max_tokens，
    # 额度不够时正文会是空的。
    max_tokens: int | None = None
    # 各厂商差异的通用出口：原样合并进请求体/请求头的 JSON 对象文本。
    # 非空时平台不再自动添加任何思考类参数，完全以这里写的为准。
    extra_body: str | None = None
    extra_headers: str | None = None


db = Database()
jobs = JobManager(db)

# 部署在反向代理的子路径下时设置，例如 AUDIT_URL_PREFIX=/chuilei/jihe。
# 留空表示直接挂在根路径（本地直连时的默认行为）。
_URL_PREFIX = os.environ.get("AUDIT_URL_PREFIX", "").strip().rstrip("/")


class _UrlPrefixMiddleware:
    """把请求路径上的子路径前缀剥掉，再交给路由匹配。

    用纯 ASGI 中间件而不是 BaseHTTPMiddleware：直接改写 scope["path"]，
    路由看到的就是剥掉前缀的路径。

    这样做的结果是后端**同时**接受 /chuilei/jihe/api/health 和 /api/health，
    反向代理剥不剥前缀都能跑，不用为了代理配置去改路由定义。

    注意：这里**不能**再设 scope["root_path"]。
    Starlette 的 StaticFiles.get_path() 会用 get_route_path() 从 scope["path"]
    里把 root_path 再剥一次；路径已经在上面剥过了，再设 root_path 就会剥不掉，
    最终去找 .../assets/assets/index-xxx.js 这种多一层目录的路径，静态资源全 404。
    代价是 /docs 里生成的 URL 不带前缀，只影响自带文档页，不影响业务。
    """

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") in ("http", "websocket"):
            path = scope.get("path", "")
            if path == _URL_PREFIX or path.startswith(_URL_PREFIX + "/"):
                scope = dict(scope)
                scope["path"] = path[len(_URL_PREFIX):] or "/"
        await self.app(scope, receive, send)


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, title="浙江移动-定级备案报告稽核系统", version="1.0.0")
app.add_middleware(AuthMiddleware, database=lambda: db)
if _URL_PREFIX:
    app.add_middleware(_UrlPrefixMiddleware)


class LoginRequest(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response):
    if "application/json" not in request.headers.get("content-type", ""):
        raise HTTPException(415, "请使用JSON登录")
    if len(payload.username) > 64 or len(payload.password) > 256:
        raise HTTPException(401, "账号或密码错误")
    address = request.client.host if request.client else "unknown"
    if not db.allow_login_attempt("ip:" + token_hash(address), 50) or not db.allow_login_attempt(
        "account:" + token_hash(payload.username.strip().casefold()), 10
    ):
        raise HTTPException(429, "登录尝试过多，请15分钟后重试")
    user = db.get_user(payload.username)
    valid = verify_password(str(user["password_hash"]) if user else "", payload.password)
    if not valid or not user or not user["enabled"]:
        raise HTTPException(401, "账号或密码错误")
    db.delete_session(token_hash(request.cookies.get(COOKIE_NAME, "")))
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    ttl = max(60, int(os.environ.get("AUDIT_SESSION_SECONDS", "28800")))
    try:
        db.new_session(token_hash(token), str(user["id"]), csrf, time.time() + ttl,
                       expected_password_hash=str(user["password_hash"]))
    except ValueError as error:
        raise HTTPException(401, str(error)) from error
    response.set_cookie(COOKIE_NAME, token, max_age=ttl, httponly=True,
                        secure=os.environ.get("AUDIT_COOKIE_SECURE", "true").lower() != "false",
                        samesite="strict", path=(_URL_PREFIX + "/") or "/")
    return {"user": public_user(user), "csrf_token": csrf}


@app.get("/api/auth/me")
def me(request: Request):
    return {"user": public_user(current_user(request)), "csrf_token": request.state.session["csrf_token"]}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    db.delete_session(token_hash(request.cookies.get(COOKIE_NAME, "")))
    response.delete_cookie(COOKIE_NAME, path=(_URL_PREFIX + "/") or "/")
    return {"ok": True}



def _job_or_404(job_id: str) -> dict[str, object]:
    # 走 jobs.get_job 而不是 db.get_job：它顺带确认工作进程还活着，
    # 进程崩了就把任务标成 failed，避免前端对着永远不动的 queued 无限轮询。
    job = jobs.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return job


def _system_or_404(system_id: str) -> dict[str, object]:
    system = db.get_system(system_id)
    if system is None:
        raise HTTPException(status_code=404, detail="系统不存在")
    return system


@app.get("/api/health")
def health() -> dict[str, object]:
    return {"ok": True, "audit_item_count": len(paired_item_ids())}


@app.get("/api/model-config")
def get_model_config() -> dict[str, object]:
    return public_view(load_model_config())


@app.put("/api/model-config")
def put_model_config(request: ModelConfigRequest) -> dict[str, object]:
    try:
        return save_model_config(request.model_dump())
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/model-config/test")
def test_model_config_endpoint(request: ModelConfigRequest) -> dict[str, object]:
    payload = request.model_dump()
    saved_key = load_model_config()["api_key"] if not payload.get("api_key") else ""
    try:
        return probe_model_connection(payload, saved_api_key=saved_key)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/systems/validate")
def validate_system(request: SourceRequest, user: dict = Depends(current_user)) -> dict[str, object]:
    try:
        return jobs.describe_source(str(checked_source(request.source_path, user, UPLOAD_ROOT)), owner_user_id=str(user["id"]))
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/systems/select-folder")
def select_system_folder() -> dict[str, object]:
    raise HTTPException(403, "请使用浏览器上传自己的材料文件夹")


@app.get("/api/systems/browse")
def browse_system_folders(path: str = "", user: dict = Depends(current_user)) -> dict[str, object]:
    """列出 BROWSE_ROOT 之下的子目录，供网页端的文件夹选择器使用。

    原生弹窗那条路在容器里走不通（见 folder_picker 里的说明），所以网页端
    自己列目录。只允许在 BROWSE_ROOT 之内浏览：请求路径先 resolve 再判断是否
    仍在根之下，这样 "../" 和指向外部的符号链接都绕不出去。
    """
    root = user_upload_root(user, UPLOAD_ROOT)
    if not root.is_dir():
        return {
            "ok": False,
            "message": (
                f"浏览根目录不存在：{root}。"
                "请把待稽核目录挂载进容器，或用 AUDIT_BROWSE_ROOT 指定别的位置。"
            ),
            "root": str(root),
            "path": "",
            "parent": None,
            "directories": [],
        }

    raw = (path or "").strip()
    target = Path(raw).expanduser() if raw else root
    if not target.is_absolute():
        target = root / target
    try:
        target = target.resolve()
    except OSError as error:
        raise HTTPException(status_code=400, detail=f"路径无法解析：{error}") from error

    if target != root and not target.is_relative_to(root):
        raise HTTPException(status_code=403, detail="只允许浏览指定的数据目录")
    if not target.is_dir():
        raise HTTPException(status_code=404, detail=f"目录不存在：{target}")

    try:
        children = sorted(
            (
                child
                for child in target.iterdir()
                if child.is_dir() and not child.name.startswith(".") and child.resolve().is_relative_to(root)
            ),
            key=lambda child: child.name,
        )
    except PermissionError as error:
        raise HTTPException(status_code=403, detail=f"没有权限读取该目录：{error}") from error

    return {
        "ok": True,
        "root": str(root),
        "path": str(target),
        "parent": str(target.parent) if target != root else None,
        "directories": [{"name": child.name, "path": str(child)} for child in children],
    }


_UPLOAD_FOLDER_MAX_CHARS = 80


def _upload_folder_name(folder: str) -> str:
    """校验浏览器传来的顶层文件夹名。文件夹名来自浏览器，不可信。"""
    name = (folder or "").strip()
    if not name or name in (".", ".."):
        raise HTTPException(status_code=400, detail="缺少文件夹名")
    if len(name) > _UPLOAD_FOLDER_MAX_CHARS:
        raise HTTPException(status_code=400, detail="文件夹名过长")
    if "/" in name or "\\" in name or "\x00" in name:
        raise HTTPException(status_code=400, detail="文件夹名不能包含路径分隔符")
    return name


def _upload_target(folder: str, user: dict) -> tuple[Path, str]:
    """解析成 UPLOAD_ROOT 下的目标目录，返回 (目录, 目录名)。

    解析后再确认仍在 UPLOAD_ROOT 之内，防止写到卷外面去。
    """
    name = _upload_folder_name(folder)
    root = user_upload_root(user, UPLOAD_ROOT)
    root.mkdir(parents=True, exist_ok=True)
    target = (root / name).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=400, detail="文件夹名越界")
    target.mkdir(parents=True, exist_ok=True)
    return target, name


def _upload_relative_path(relative: str) -> PurePosixPath:
    """清洗浏览器给的相对路径，只保留安全的目录层级。

    webkitRelativePath 形如 "某系统/材料/文件.pdf"。逐段校验而不是直接拼接，
    任何一段是 .. 或 . 都拒绝，从根上杜绝目录穿越。
    """
    raw = (relative or "").replace("\\", "/").strip()
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if not parts:
        raise HTTPException(status_code=400, detail="缺少文件相对路径")
    if any(part == ".." for part in parts):
        raise HTTPException(status_code=400, detail="相对路径不能包含 ..")
    if any("\x00" in part for part in parts):
        raise HTTPException(status_code=400, detail="相对路径含非法字符")
    return PurePosixPath(*parts)


def _strip_root_segment(relative: PurePosixPath, folder_name: str) -> PurePosixPath:
    """去掉相对路径开头与目标文件夹重名的那一段。

    浏览器的 webkitRelativePath 会把用户选中的顶层文件夹名也带上
    （"某系统/材料/a.pdf"），而目标目录已经用了这个文件夹名，直接拼接会变成
    某系统/某系统/材料/a.pdf，多套一层。这里把首段对齐掉。
    """
    if relative.parts and relative.parts[0] == folder_name:
        trimmed = PurePosixPath(*relative.parts[1:])
        if not trimmed.parts:
            raise HTTPException(status_code=400, detail="相对路径没有文件名")
        return trimmed
    return relative


@app.post("/api/systems/upload")
async def upload_system_file(
    request: Request,
    folder: str = Query(...),
    relative_path: str = Query(...),
    user: dict = Depends(current_user),
) -> dict[str, object]:
    """接收网页端上传的单个文件，按原目录结构落到 UPLOAD_ROOT 之下。

    用户在**自己的电脑上**用浏览器的文件夹选择框选目录，浏览器逐个把文件连同
    webkitRelativePath 传上来，这里还原成服务器上的目录树，之后交给原有的
    校验/稽核流程，与服务器本地目录走的是同一条路径。

    文件走**原始请求体**（application/octet-stream）而不是 multipart：
    multipart 需要 python-multipart 依赖，而镜像里没有、目标服务器又装不了；
    原始体还能边收边写盘，大文件不用整个读进内存。
    目录名和相对路径放在查询参数里。
    """
    target, folder_name = _upload_target(folder, user)
    relative = _strip_root_segment(_upload_relative_path(relative_path), folder_name)
    destination = target / relative

    # 相对路径已逐段校验过，这里再确认最终落点仍在目标目录内，作为兜底
    if not destination.resolve().is_relative_to(target):
        raise HTTPException(status_code=400, detail="目标路径越界")

    destination.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    try:
        with destination.open("wb") as handle:
            async for chunk in request.stream():
                written += len(chunk)
                handle.write(chunk)
    except OSError as error:
        raise HTTPException(status_code=500, detail=f"写入失败：{error}") from error

    return {"ok": True, "relative_path": str(relative), "size": written}


class UploadFinishRequest(BaseModel):
    folder: str


@app.post("/api/systems/upload/finish")
def finish_upload(request: UploadFinishRequest, user: dict = Depends(current_user)) -> dict[str, object]:
    """上传结束：返回可直接交给稽核流程的服务器路径。"""
    target, _ = _upload_target(request.folder, user)
    checked_source(str(target), user, UPLOAD_ROOT)
    files = [path for path in target.rglob("*") if path.is_file()]
    if not files:
        raise HTTPException(status_code=400, detail="没有收到任何文件")
    return {
        "ok": True,
        "source_path": str(target),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
    }


@app.get("/api/systems")
def recent_systems(user: dict = Depends(current_user)) -> list[dict[str, object]]:
    return db.list_systems(owner_user_id=str(user["id"]))


@app.post("/api/jobs", status_code=202)
def create_job(request: SourceRequest, user: dict = Depends(current_user)) -> dict[str, object]:
    try:
        return jobs.create_job(str(checked_source(request.source_path, user, UPLOAD_ROOT)), owner_user_id=str(user["id"]))
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except RuntimeError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    return _job_or_404(job_id)


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, object]:
    try:
        return jobs.cancel_job(job_id)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


def _parse_last_event_id(value: str | None) -> int:
    if not value:
        return 0
    try:
        return max(int(value.strip()), 0)
    except (TypeError, ValueError):
        return 0


def _read_event_records(target: Path, offset: int) -> tuple[int, list[tuple[int, str]]]:
    """Read complete JSONL records and identify them by UTF-8 byte offset."""
    try:
        size = target.stat().st_size
    except OSError:
        return offset, []
    cursor = offset if 0 <= offset <= size else 0
    records: list[tuple[int, str]] = []
    try:
        with target.open("rb") as stream:
            stream.seek(cursor)
            while True:
                raw = stream.readline()
                if not raw or not raw.endswith(b"\n"):
                    break
                cursor = stream.tell()
                try:
                    line = raw[:-1].decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if line.endswith("\r"):
                    line = line[:-1]
                if line:
                    records.append((cursor, line))
    except OSError:
        return offset, []
    return cursor, records


@app.get("/api/jobs/{job_id}/events")
async def stream_events(
    job_id: str,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    request: Request = None,
) -> StreamingResponse:
    _job_or_404(job_id)
    target = event_path(job_id)

    async def generate():
        offset = _parse_last_event_id(last_event_id)
        idle = 0
        while True:
            if request is not None and not db.get_session(token_hash(request.cookies.get(COOKIE_NAME, ""))):
                yield 'event: auth_expired\ndata: {}\n\n'
                break
            offset, records = _read_event_records(target, offset)
            for event_offset, line in records:
                offset = event_offset
                idle = 0
                yield f"id: {event_offset}\ndata: {line}\n\n"
            job = db.get_job(job_id)
            terminal = job and job.get("status") in {
                "completed",
                "completed_with_errors",
                "failed",
                "cancelled",
            }
            try:
                drained = not target.is_file() or target.stat().st_size <= offset
            except OSError:
                drained = False
            if terminal and drained:
                yield f"event: end\ndata: {json.dumps(job, ensure_ascii=False)}\n\n"
                break
            idle += 1
            if idle % 15 == 0:
                yield ": heartbeat\n\n"
            await asyncio.sleep(0.4)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _load_summary(system: dict[str, object]) -> tuple[Path, dict[str, object]]:
    path = Path(str(system["output_dir"])) / "summary.json"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="该系统还没有可用结果")
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    return path, normalize_audit_summary(payload)


def _load_result_json(output: Path, relative: object) -> dict[str, object] | None:
    if not relative:
        return None
    target = (output / str(relative)).resolve()
    try:
        target.relative_to(output.resolve())
    except ValueError as error:
        raise HTTPException(status_code=400, detail="结果路径越界") from error
    if not target.is_file():
        return None
    try:
        payload = json.loads(target.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


@app.get("/api/systems/{system_id}/summary")
def system_summary(system_id: str) -> dict[str, object]:
    _, payload = _load_summary(_system_or_404(system_id))
    return payload


@app.get("/api/systems/{system_id}/audits/{item_id}")
def audit_detail(system_id: str, item_id: str) -> dict[str, object]:
    system = _system_or_404(system_id)
    _, summary = _load_summary(system)
    normalized = item_id.strip().upper().replace("_", "-")
    if normalized not in paired_item_ids():
        raise HTTPException(status_code=404, detail="稽核项不存在")
    items = summary.get("audit", {}).get("items", [])
    record = next((item for item in items if item.get("item_id") == normalized), None)
    if record is None:
        raise HTTPException(status_code=404, detail="稽核结果不存在")
    output = Path(str(system["output_dir"]))
    response = {
        "summary": record,
        "fields": None,
        "result": None,
        "ai_narration": record.get("ai_narration"),
    }
    for key, target_key in (("fields_file", "fields"), ("result_file", "result")):
        response[target_key] = _load_result_json(output, record.get(key))

    ai_relative = record.get("ai_file") or record.get("ai_narration_file")
    fields_relative = record.get("fields_file")
    if not ai_relative and fields_relative:
        ai_relative = str(Path(str(fields_relative)).parent / "AI复述.json")
    narration = _load_result_json(output, ai_relative)
    if narration is not None:
        response["ai_narration"] = narration
        record["ai_narration"] = narration
    return response


@app.get("/api/systems/{system_id}/report")
def download_report(system_id: str) -> FileResponse:
    system = _system_or_404(system_id)
    _, summary = _load_summary(system)
    report = Path(str(system["output_dir"])) / "汇总报告.html"
    generate_report(summary, report)
    return FileResponse(report, media_type="text/html", filename=f"{system['display_name']}-稽核汇总报告.html")


FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"
if (FRONTEND_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def frontend(path: str = ""):
    if path == "api" or path.startswith("api/"):
        raise HTTPException(404, "接口不存在")
    index = FRONTEND_DIST / "index.html"
    if index.is_file():
        # The JavaScript bundle is content-hashed, but index.html selects which hash
        # to load. Never let a reopened/reloaded tab keep an obsolete application
        # shell after the platform has been upgraded.
        return FileResponse(
            index,
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return HTMLResponse(
        "<h1>前端尚未构建</h1><p>请先在 frontend 目录完成构建。</p>",
        status_code=503,
    )
