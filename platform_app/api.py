from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .audit_service import normalize_audit_summary, paired_item_ids
from .config import PROJECT_ROOT, ai_config_status
from .database import Database
from .events import event_path
from .folder_picker import FolderPickerError, choose_system_folder
from .job_manager import JobManager
from .model_config import (
    DEFAULT_MODEL,
    load_model_config,
    probe_model_connection,
    public_view,
    save_model_config,
)
from .report import generate_report


class SourceRequest(BaseModel):
    source_path: str


class ModelConfigRequest(BaseModel):
    base_url: str = "https://open.bigmodel.cn/api/paas/v4"
    api_key: str = ""
    model: str = DEFAULT_MODEL
    timeout_seconds: int = 120


db = Database()
jobs = JobManager(db)
app = FastAPI(title="浙江移动-定级备案报告稽核系统", version="1.0.0")


def _job_or_404(job_id: str) -> dict[str, object]:
    job = db.get_job(job_id)
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
    status = ai_config_status()
    public_ai = {
        key: status[key]
        for key in (
            "enabled",
            "configured",
            "available",
            "model",
            "endpoint_host",
            "prompt_version",
            "max_input_bytes",
        )
        if key in status
    }
    return {
        "ok": True,
        "audit_item_count": len(paired_item_ids()),
        "ai": public_ai,
    }


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
def validate_system(request: SourceRequest) -> dict[str, object]:
    try:
        return jobs.describe_source(request.source_path)
    except (FileNotFoundError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@app.post("/api/systems/select-folder")
def select_system_folder() -> dict[str, object]:
    try:
        selected = choose_system_folder()
    except FolderPickerError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    return {"cancelled": selected is None, "source_path": str(selected) if selected else None}


@app.get("/api/systems")
def recent_systems() -> list[dict[str, object]]:
    return db.list_systems()


@app.post("/api/jobs", status_code=202)
def create_job(request: SourceRequest) -> dict[str, object]:
    try:
        return jobs.create_job(request.source_path)
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
) -> StreamingResponse:
    _job_or_404(job_id)
    target = event_path(job_id)

    async def generate():
        offset = _parse_last_event_id(last_event_id)
        idle = 0
        while True:
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
