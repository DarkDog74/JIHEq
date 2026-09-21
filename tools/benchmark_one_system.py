"""Measure one full frontend-equivalent audit, including streaming explanations.

Provider usage is recorded at the transport boundary, even when downstream
explanation validation fails. Existing prompts/rules/retries are unchanged.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

# 保证直接运行 tools/ 下的脚本时仍能导入项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.benchmark_token_usage import RecordedTransport, UsageRecorder, summarize, write_json


class StreamResponse:
    def __init__(self, response, record):
        self.response, self.record = response, record

    def __getattr__(self, name):
        return getattr(self.response, name)

    def iter_lines(self):
        data = []

        def capture():
            if not data:
                return
            try:
                payload = json.loads("\n".join(data))
            except ValueError:
                return
            if not isinstance(payload, dict):
                return
            if isinstance(payload.get("usage"), dict):
                # Streaming usage snapshots are cumulative, not additive.
                self.record["usage"] = payload["usage"]
            for name in ("model", "id"):
                if payload.get(name):
                    self.record[f"response_{name}"] = payload[name]
            reasons = [x["finish_reason"] for x in (payload.get("choices") or [])
                       if isinstance(x, dict) and x.get("finish_reason")]
            if reasons:
                self.record["finish_reasons"] = reasons

        for line in self.response.iter_lines():
            if not line:
                capture()
                data.clear()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
            yield line
        capture()
        self.record["stream_consumed"] = True


class StreamClient:
    def __init__(self, client, recorder):
        self.client, self.recorder = client, recorder

    @contextmanager
    def stream(self, *args, **kwargs):
        started = time.perf_counter()
        record = {"sequence": len(self.recorder.records) + 1, **self.recorder.context,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "phase": "explanation", "usage": None, "stream_consumed": False}
        try:
            request = json.loads(kwargs.get("content") or b"{}")
            record["requested_model"] = request.get("model")
            with self.client.stream(*args, **kwargs) as response:
                record["http_status"] = response.status_code
                yield StreamResponse(response, record)
        except Exception as error:
            record["transport_or_stream_error"] = type(error).__name__
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            self.recorder.records.append(record)
            with self.recorder.ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def run(source: Path, output: Path):
    import httpx
    from core import model_client
    from parsing_engine import run_unit_parse
    from platform_app.ai_service import AuditAIService
    from platform_app.audit_service import run_unit_audits
    from platform_app.config import load_ai_settings
    from platform_app.worker import _run_ai_item

    source, output = source.resolve(), output.resolve()
    if not source.is_dir():
        raise FileNotFoundError(source)
    settings = load_ai_settings()
    if not settings.available:
        raise RuntimeError("当前模型未启用或未配置")
    output.mkdir(parents=True, exist_ok=False)
    project = Path(__file__).resolve().parents[1]
    files = [project / "core" / "model_client.py"]
    for directory in ("audit_rules", "extractors", "platform_app", "parser", "parsing_engine"):
        files.extend((project / directory).glob("*.py"))
    manifest = {"source": str(source), "model": settings.model,
                "started_at": datetime.now(timezone.utc).isoformat(),
                "mode": "raw_source_parse_then_frontend_audit_and_explanations",
                "api_settings": {"max_tokens_explanation": settings.max_tokens,
                                 "timeout_seconds_explanation": settings.timeout_seconds},
                "code_sha256": {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in files}}
    write_json(output / "运行清单.json", manifest)
    recorder = UsageRecorder(output / "模型接口用量.jsonl", model_client.httpx)
    recorder.context = {"system": source.name, "phase": "decision"}
    item_timings, ai_outcomes = [], []
    item_started = None

    def emit(event):
        nonlocal item_started
        if event.get("type") == "audit_started":
            item_started = time.perf_counter()
            recorder.context.update(item_id=event["item_id"], phase="decision")
            print(f"[{event['current']}/{event['total']}] {event['item_id']} 开始", flush=True)

    def narrate(payload):
        result = _run_ai_item(service, payload, lambda event: None)
        outcome = {"item_id": payload["item_id"], "status": result.get("status"),
                   "cached": result.get("cached", False), "error_code": result.get("error_code"),
                   "precomputed": bool(payload.get("rule_result", {}).get("_precomputed_narration"))}
        ai_outcomes.append(outcome)
        item_timings.append({**outcome, "seconds": time.perf_counter() - item_started})
        write_json(output / "实时进度.json", {"items": item_timings, "usage": summarize(recorder.records)})
        print(f"{payload['item_id']} 完成：说明={outcome['status']}，"
              f"累计请求={len(recorder.records)}，有用量={summarize(recorder.records)['usage_reported_attempts']}", flush=True)
        return result

    started = time.perf_counter()
    parse_seconds = None
    audit = None
    try:
        print("开始解析原始材料", flush=True)
        parsed = run_unit_parse(source, output / "解析结果")
        parse_seconds = time.perf_counter() - started
        write_json(output / "解析状态.json", {"exit_code": parsed.exit_code,
                                         "failed_files": parsed.failed_files,
                                         "seconds": parse_seconds})
        print(f"解析完成：exit={parsed.exit_code}，耗时={parse_seconds:.1f}秒", flush=True)
        with httpx.Client(timeout=httpx.Timeout(settings.timeout_seconds, connect=10),
                          follow_redirects=False) as client:
            service = AuditAIService(settings=settings, client=StreamClient(client, recorder))
            with patch.object(model_client, "httpx", RecordedTransport(model_client.httpx, recorder)):
                audit = run_unit_audits(output / "解析结果", output / "稽核结果",
                                        on_event=emit, on_item_ready=narrate)
                write_json(output / "前端稽核汇总.json", audit)
    finally:
        changed = [name for name, digest in manifest["code_sha256"].items()
                   if hashlib.sha256((project / name).read_bytes()).hexdigest() != digest]
        write_json(output / "实测汇总.json", {
            "complete": audit is not None, "system": source.name,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - started, "parse_seconds": parse_seconds,
            "completed_items": len(ai_outcomes), "ai_outcomes": ai_outcomes,
            "usage": summarize(recorder.records), "code_changed_during_run": changed,
            "scope": "decision HTTP attempts + frontend explanation streaming; excludes any parser/OCR billing",
        })
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.source, args.output))
