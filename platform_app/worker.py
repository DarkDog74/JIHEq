from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import traceback
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path

from parsing_engine import preview_source, run_unit_parse

from .ai_service import PROMPT_VERSION, create_ai_service
from .audit_service import run_unit_audits
from .config import DATABASE_PATH, STAGING_ROOT, ensure_storage
from .database import Database
from .events import append_event
from .report import generate_report


TECHNICAL_REASON_CODES = {
    "blocked_by_parse_error",
    "extraction_error",
    "execution_error",
}


def _ai_event_context(payload: Mapping[str, object]) -> dict[str, object]:
    normalized = payload.get("normalized")
    normalized = normalized if isinstance(normalized, Mapping) else {}
    return {
        "stage": "auditing",
        "item_id": str(payload.get("item_id") or ""),
        "item_name": str(payload.get("item_name") or payload.get("item_id") or ""),
        "current": int(payload.get("current") or 0),
        "total": int(payload.get("total") or 0),
        "audit_status": str(normalized.get("status") or ""),
        "conclusion": str(normalized.get("message") or ""),
        "extracted_summary": normalized.get("extracted_summary", []),
    }


def _fallback_ai_result(
    payload: Mapping[str, object],
    service: object | None,
    *,
    error_code: str,
) -> dict[str, object]:
    settings = getattr(service, "settings", None)
    result: dict[str, object] = {
        "status": "failed",
        "model": str(getattr(settings, "model", "glm-5v-turbo")),
        "text": "AI说明生成失败：AI服务内部异常",
        "error_code": error_code,
        "error_message": "AI服务内部异常",
        "prompt_version": PROMPT_VERSION,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cached": False,
        "usage": {},
    }
    item_dir = payload.get("item_dir")
    if isinstance(item_dir, (str, Path)):
        target = Path(item_dir) / "AI复述.json"
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_symlink():
                target.write_text(
                    json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
        except OSError:
            pass
    return result


def _persist_ai_attachment(payload: Mapping[str, object], result: Mapping[str, object]) -> None:
    item_dir = payload.get("item_dir")
    if not isinstance(item_dir, (str, Path)):
        return
    target = Path(item_dir) / "AI复述.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            return
        target.write_text(
            json.dumps(dict(result), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError:
        return


def _precomputed_ai_result(payload: Mapping[str, object]) -> dict[str, object] | None:
    rule_result = payload.get("rule_result")
    if not isinstance(rule_result, Mapping):
        return None
    raw = rule_result.get("_precomputed_narration")
    if not isinstance(raw, Mapping) or raw.get("attempted") is not True:
        return None
    status = "completed" if raw.get("status") == "completed" else "failed"
    digest_source = {
        "item_id": payload.get("item_id"),
        "fields": payload.get("fields"),
        "rule_result": {
            key: value for key, value in rule_result.items() if key != "_precomputed_narration"
        },
    }
    digest = hashlib.sha256(
        json.dumps(digest_source, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    result: dict[str, object] = {
        "status": status,
        "model": str(raw.get("model") or "glm-5v-turbo"),
        "text": str(raw.get("text") or "说明生成失败：语义识别未完成"),
        "input_sha256": digest,
        "prompt_version": "semantic-analysis-v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "cached": False,
        "usage": raw.get("usage") if isinstance(raw.get("usage"), Mapping) else {},
    }
    if status != "completed":
        result["error_code"] = str(raw.get("error_code") or "precomputed_analysis_failed")
        result["error_message"] = str(raw.get("text") or "语义识别未完成")
    _persist_ai_attachment(payload, result)
    return result


def _run_ai_item(
    service: object | None,
    payload: Mapping[str, object],
    emit: Callable[[dict[str, object]], None],
) -> dict[str, object]:
    """Generate one narration synchronously; display failures never alter audit state."""
    context = _ai_event_context(payload)
    chunk_seq = 0

    def safe_emit(event: dict[str, object]) -> None:
        try:
            emit(event)
        except Exception:
            # Event delivery is optional and must not affect the fixed audit result.
            return

    safe_emit(
        {
            "type": "ai_started",
            **context,
            "chunk_seq": chunk_seq,
            "message": f"正在生成 {context['item_id']} 的 AI 说明",
        }
    )

    def on_delta(delta: str) -> None:
        nonlocal chunk_seq
        text = str(delta or "")
        if not text:
            return
        chunk_seq += 1
        safe_emit(
            {
                "type": "ai_delta",
                **context,
                "chunk_seq": chunk_seq,
                "delta": text,
            }
        )

    narration = _precomputed_ai_result(payload)
    if narration is not None:
        if narration.get("status") == "completed":
            on_delta(str(narration.get("text") or ""))
    elif service is None:
        narration = _fallback_ai_result(
            payload, service, error_code="service_initialization_error"
        )
    else:
        try:
            generate = getattr(service, "generate")
            generated = generate(payload, on_delta=on_delta)
            if not isinstance(generated, dict):
                raise TypeError("AI服务返回格式无效")
            narration = generated
        except Exception:
            narration = _fallback_ai_result(
                payload, service, error_code="service_internal_error"
            )

    if narration.get("status") == "completed":
        text = str(narration.get("text") or "")
        if chunk_seq == 0 and text:
            on_delta(text)
        chunk_seq += 1
        safe_emit(
            {
                "type": "ai_completed",
                **context,
                "chunk_seq": chunk_seq,
                "narration": narration,
                "message": "AI说明已从缓存读取"
                if narration.get("cached")
                else "AI说明已生成",
            }
        )
    else:
        chunk_seq += 1
        safe_emit(
            {
                "type": "ai_failed",
                **context,
                "chunk_seq": chunk_seq,
                "narration": narration,
                "error_type": str(
                    narration.get("error_code") or narration.get("status") or "failed"
                ),
                "message": str(
                    narration.get("error_message")
                    or narration.get("text")
                    or "AI说明生成失败"
                ),
            }
        )
    return narration


def _seed_ai_cache(
    service: object | None,
    payload: Mapping[str, object],
    previous_output: Path,
) -> None:
    """Copy only a prior AI attachment into staging so the service can validate it."""
    if service is None or not bool(getattr(service, "available", False)):
        return
    item_id = str(payload.get("item_id") or "")
    item_dir = payload.get("item_dir")
    if not item_id or not isinstance(item_dir, (str, Path)):
        return
    previous_root = Path(previous_output).resolve()
    previous_candidate = previous_root / "audits" / item_id / "AI复述.json"
    if previous_candidate.is_symlink():
        return
    previous = previous_candidate.resolve()
    try:
        previous.relative_to(previous_root)
    except ValueError:
        return
    target = Path(item_dir) / "AI复述.json"
    if (
        not previous.is_file()
        or target.is_symlink()
        or target.exists()
    ):
        return
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(previous, target)
    except OSError:
        # A cache seed is optional; generation proceeds normally without it.
        return


def _should_narrate(service: object | None) -> bool:
    """A deliberate runtime disable restores the exact pre-AI audit flow."""
    if service is None:
        return True
    settings = getattr(service, "settings", None)
    return bool(getattr(settings, "enabled", True))


def _safe_remove(path: Path, expected_parent: Path) -> None:
    resolved_parent = expected_parent.resolve()
    resolved = path.resolve(strict=False)
    if resolved.parent != resolved_parent or path.is_symlink():
        raise ValueError(f"拒绝清理非预期目录：{path}")
    if path.exists():
        shutil.rmtree(path)


def _publish(staging: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.is_symlink():
        raise ValueError(f"拒绝覆盖符号链接：{final}")
    backup = final.with_name(f".{final.name}.previous")
    _safe_remove(backup, final.parent)
    had_previous = final.exists()
    if had_previous:
        final.rename(backup)
    try:
        staging.rename(final)
    except Exception:
        if had_previous and backup.exists() and not final.exists():
            backup.rename(final)
        raise
    _safe_remove(backup, final.parent)


def run_job(job_id: str, database_path: Path) -> int:
    ensure_storage()
    db = Database(database_path)
    job = db.get_job(job_id)
    if job is None:
        raise KeyError(f"任务不存在：{job_id}")
    system = db.get_system(str(job["system_id"]))
    if system is None:
        raise KeyError(f"系统不存在：{job['system_id']}")

    source = Path(str(job["source_path"])).resolve()
    final = Path(str(system["output_dir"])).resolve()
    staging = (STAGING_ROOT / job_id).resolve()
    _safe_remove(staging, STAGING_ROOT)
    staging.mkdir(parents=True)
    parsed_dir = staging / "parsed"
    audits_dir = staging / "audits"

    def emit(payload: dict[str, object]) -> None:
        append_event(job_id, payload)
        event_type = str(payload.get("type") or "")
        if event_type == "audit_started":
            current = int(payload.get("current") or 0)
            total = max(int(payload.get("total") or 1), 1)
            db.update_job(
                job_id,
                stage="auditing",
                progress=0.35 + 0.35 * ((current - 1) / total),
                message=str(payload.get("message") or "正在稽核"),
            )
        elif event_type == "audit_complete":
            current = int(payload.get("current") or 0)
            total = max(int(payload.get("total") or 1), 1)
            db.update_job(
                job_id,
                stage="auditing",
                progress=0.35 + 0.60 * (current / total),
                message=f"{payload.get('item_id') or '当前稽核项'} 已完成规则与 LLM 处理",
            )

    db.update_job(job_id, status="running", stage="parsing", progress=0.03, message="正在分析系统材料")
    emit({"type": "job_started", "stage": "parsing", "message": "单系统解析与稽核任务已启动"})
    preview = preview_source(source)
    parse_result = run_unit_parse(source, parsed_dir, on_event=emit)
    db.update_job(job_id, stage="auditing", progress=0.35, message="解析完成，开始运行全部稽核项")
    try:
        ai_service = create_ai_service()
    except Exception:
        # AI is an optional attachment. Initialization failure must not stop auditing.
        ai_service = None

    def narrate_before_publish(payload: dict[str, object]) -> dict[str, object]:
        item_id = str(payload.get("item_id") or "")
        current = int(payload.get("current") or 0)
        total = max(int(payload.get("total") or 1), 1)
        db.update_job(
            job_id,
            stage="auditing",
            progress=0.35 + 0.60 * ((current - 1) / total),
            message=f"规则已完成，LLM 正在处理 {item_id}",
        )
        _seed_ai_cache(ai_service, payload, final)
        # Do not persist partial model events. The single audit_complete record
        # below is the public queue entry and is written only after LLM returns.
        return _run_ai_item(ai_service, payload, lambda _event: None)

    audit_summary = run_unit_audits(
        parsed_dir,
        audits_dir,
        on_event=emit,
        on_item_ready=narrate_before_publish,
    )

    files = parse_result.analysis.get("files", [])
    file_count = len(files) if isinstance(files, list) else 0
    failed_files = parse_result.failed_files
    summary: dict[str, object] = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "system": {
            "id": system["id"],
            "name": system["display_name"],
            "department": system["department"],
            "source_path": system["source_path"],
        },
        "parse": {
            "exit_code": parse_result.exit_code,
            "file_count": file_count,
            "recognized_count": preview["recognized_count"],
            "category_counts": preview["category_counts"],
            "failed_file_count": len(failed_files),
            "failed_files": failed_files,
            "analysis_file": "parsed/文件分析.json",
        },
        "audit": audit_summary,
    }
    (staging / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    db.update_job(job_id, stage="reporting", progress=0.97, message="正在生成离线汇总报告")
    emit({"type": "stage", "stage": "reporting", "message": "正在生成独立HTML汇总报告"})
    generate_report(summary, staging / "汇总报告.html")
    _publish(staging, final)

    item_reason_codes = {
        str(item.get("reason_code"))
        for item in audit_summary.get("items", [])
        if isinstance(item, dict)
    }
    has_errors = bool(failed_files) or bool(
        item_reason_codes & TECHNICAL_REASON_CODES
    )
    status = "completed_with_errors" if has_errors else "completed"
    message = "处理完成，存在需要关注的解析或提取问题" if has_errors else "单系统解析与全部稽核已完成"
    db.update_job(
        job_id,
        status=status,
        stage="completed",
        progress=1,
        message=message,
        result_path=str(final / "summary.json"),
        error=None,
    )
    emit({"type": "job_complete", "stage": "completed", "status": status, "message": message})
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    args = parser.parse_args(argv)
    try:
        return run_job(args.job_id, args.database)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        db = Database(args.database)
        db.update_job(
            args.job_id,
            status="failed",
            stage="failed",
            message="任务执行失败",
            error=f"{type(error).__name__}: {error}",
        )
        append_event(
            args.job_id,
            {
                "type": "job_failed",
                "stage": "failed",
                "message": f"{type(error).__name__}: {error}",
                "technical_detail": traceback.format_exc(),
            },
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
