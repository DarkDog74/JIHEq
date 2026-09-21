"""Run an isolated 20-system benchmark; retain provider usage for every HTTP attempt.

Does not change production rules, prompts, retry behaviour, or existing outputs.
Only the benchmark process wraps model_client's HTTP transport. No secrets,
prompts, image data, or response text are written to the usage ledger.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

# 保证直接运行 tools/ 下的脚本时仍能导入项目根目录模块
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


DEPARTMENTS = ("传输部", "核心网部", "互联网部", "网管支撑部")


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


class UsageRecorder:
    def __init__(self, ledger, transport):
        self.ledger = Path(ledger)
        self.transport = transport
        self.context = {}
        self.records = []

    def post(self, *args, **kwargs):
        started = time.perf_counter()
        payload = kwargs.get("json") or {}
        record = {"sequence": len(self.records) + 1, **self.context,
                  "started_at": datetime.now(timezone.utc).isoformat(),
                  "requested_model": payload.get("model"), "usage": None}
        try:
            response = self.transport.post(*args, **kwargs)
            record["http_status"] = response.status_code
            try:
                body = response.json()
            except (ValueError, TypeError):
                body = {}
            if isinstance(body, dict):
                record["usage"] = body.get("usage")
                record["response_model"] = body.get("model")
                record["response_id"] = body.get("id")
                record["finish_reasons"] = [x.get("finish_reason") for x in
                    (body.get("choices") or []) if isinstance(x, dict)]
            return response
        except Exception as error:
            # Exception messages may include credentials/URLs. Retain type only.
            record["transport_error"] = type(error).__name__
            raise
        finally:
            record["elapsed_ms"] = round((time.perf_counter() - started) * 1000)
            self.records.append(record)
            with self.ledger.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class RecordedTransport:
    def __init__(self, original, recorder):
        self.original = original
        self.post = recorder.post

    def __getattr__(self, name):
        return getattr(self.original, name)


def summarize(records):
    """Unknown usage stays unknown. Detail tokens are not added to their parent."""
    result = {"http_attempts": len(records), "usage_reported_attempts": 0,
              "missing_usage_attempts": 0, "reported_token_sums": {},
              "http_elapsed_ms": sum(r["elapsed_ms"] for r in records)}
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict) or not usage:
            result["missing_usage_attempts"] += 1
            continue
        result["usage_reported_attempts"] += 1
        for key, value in usage.items():
            if isinstance(value, int) and not isinstance(value, bool):
                sums = result["reported_token_sums"]
                sums[key] = sums.get(key, 0) + value
            elif isinstance(value, dict):
                for subkey, subvalue in value.items():
                    if isinstance(subvalue, int) and not isinstance(subvalue, bool):
                        name = f"{key}.{subkey}"
                        sums = result["reported_token_sums"]
                        sums[name] = sums.get(name, 0) + subvalue
    return result


def run(source_root, output_root):
    import main as app
    from core import model_client
    from platform_app.model_config import load_model_config

    source_root, output_root = Path(source_root).resolve(), Path(output_root).resolve()
    units = {dep: app._discover_source_unit_directories(source_root / dep, 1)
             for dep in DEPARTMENTS}
    if sum(map(len, units.values())) != 20:
        raise ValueError("本次对比必须正好包含20个系统，发现数量不符，停止运行")
    output_root.mkdir(parents=True, exist_ok=False)
    config = load_model_config()
    project = Path(__file__).resolve().parents[1]
    source_files = [project / "main.py", project / "core" / "model_client.py"]
    for directory in ("audit_rules", "extractors", "parser", "parsing_engine"):
        source_files.extend((project / directory).glob("*.py"))
    manifest = {"started_at": datetime.now(timezone.utc).isoformat(),
                "model": config.get("model"), "source_root": str(source_root),
                "systems": {dep: [str(p) for p in values] for dep, values in units.items()},
                "system_count": 20, "mode": "full_source_parse_and_all_audits",
                "scope": "audit model HTTP attempts; parser subprocess and frontend explanation calls excluded",
                "code_sha256": {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in source_files}}
    write_json(output_root / "运行清单.json", manifest)
    recorder = UsageRecorder(output_root / "模型接口用量.jsonl", model_client.httpx)
    original_batch, original_parse = app.run_batch, app.run_unit_parse
    timings, departments = [], []

    def measured_batch(**kwargs):
        recorder.context.update(item_id=kwargs["item_id"], unit=Path(kwargs["dataset_dir"]).name)
        start = time.perf_counter()
        try:
            return original_batch(**kwargs)
        finally:
            timing = {**recorder.context, "phase": "audit_item", "seconds": time.perf_counter() - start}
            timings.append(timing)
            with (output_root / "流程计时.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(timing, ensure_ascii=False) + "\n")

    def measured_parse(source, output, *args, **kwargs):
        start = time.perf_counter()
        try:
            return original_parse(source, output, *args, **kwargs)
        finally:
            timing = {"department": recorder.context["department"], "unit": Path(source).name,
                      "phase": "parse", "seconds": time.perf_counter() - start}
            timings.append(timing)
            with (output_root / "流程计时.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(timing, ensure_ascii=False) + "\n")

    started = time.perf_counter()
    try:
        with patch.object(model_client, "httpx", RecordedTransport(model_client.httpx, recorder)), \
             patch.object(app, "run_batch", measured_batch), \
             patch.object(app, "run_unit_parse", measured_parse):
            for dep in DEPARTMENTS:
                recorder.context = {"department": dep}
                start = time.perf_counter()
                print(f"开始 {dep}，系统数={len(units[dep])}", flush=True)
                with (output_root / f"{dep}_终端日志.txt").open("w", encoding="utf-8", buffering=1) as log, \
                     redirect_stdout(log), redirect_stderr(log):
                    flow = app.run_source_batch_all(source_dir=source_root / dep,
                               output_dir=output_root / dep, unit_depth=1, overwrite=False)
                    print(app.format_source_batch_all_summary(flow))
                    evaluation = app.run_source_batch_evaluation(source_dir=source_root / dep,
                                                                result_dir=output_root / dep)
                    print(app.format_source_batch_evaluation_summary(evaluation))
                departments.append({"department": dep, "seconds": time.perf_counter() - start,
                                    "system_count": len(units[dep]), "evaluation": evaluation})
                write_json(output_root / "进度汇总.json", {"departments": departments,
                                                          "usage": summarize(recorder.records)})
                print(f"完成 {dep}，累计HTTP请求={len(recorder.records)}", flush=True)
    finally:
        changed = [name for name, digest in manifest["code_sha256"].items()
                   if hashlib.sha256((project / name).read_bytes()).hexdigest() != digest]
        write_json(output_root / "实测汇总.json", {
            "complete": len(departments) == len(DEPARTMENTS),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "wall_seconds": time.perf_counter() - started,
            "completed_system_count": sum(d["system_count"] for d in departments),
            "departments": departments, "usage": summarize(recorder.records),
            "code_changed_during_run": changed,
            "scope": manifest["scope"],
        })
    return output_root


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.source_root, args.output_root))
