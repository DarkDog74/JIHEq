from __future__ import annotations

import hashlib
import os
import re
import signal
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from parsing_engine import preview_source

from .config import DATABASE_PATH, OUTPUT_ROOT, PROJECT_ROOT, STATE_ROOT, ensure_storage
from .database import Database
from .events import append_event, event_path


ACTIVE_STATUSES = {"queued", "running", "cancelling"}


def system_id_for(source: Path) -> str:
    return hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:12]


def safe_output_name(name: str, system_id: str) -> str:
    cleaned = re.sub(r"[^\w\-\u4e00-\u9fff]+", "-", name, flags=re.UNICODE).strip("-")
    return f"{cleaned[:72] or 'system'}-{system_id}"


class JobManager:
    def __init__(self, database: Database | None = None) -> None:
        ensure_storage()
        self.db = database or Database()
        self._processes: dict[str, subprocess.Popen[bytes]] = {}
        self._recover_stale_job()

    def _reap_process(self, job_id: str, process: subprocess.Popen[bytes]) -> None:
        process.wait()
        self._processes.pop(job_id, None)

    def _recover_stale_job(self) -> None:
        active = self.db.get_active_job()
        if not active:
            return
        pid = active.get("pid")
        if not pid:
            self.db.update_job(str(active["id"]), status="failed", stage="failed", message="服务重启前任务未启动完成", error="stale job")
            return
        try:
            os.kill(int(pid), 0)
        except (ProcessLookupError, PermissionError):
            self.db.update_job(str(active["id"]), status="failed", stage="failed", message="服务重启时发现任务已中断", error="worker process not running")

    def describe_source(self, source_path: str) -> dict[str, object]:
        preview = preview_source(Path(source_path))
        source = Path(str(preview["source_path"]))
        system_id = system_id_for(source)
        output_dir = OUTPUT_ROOT / safe_output_name(source.name, system_id)
        existing = self.db.get_system(system_id)
        return {
            **preview,
            "system_id": system_id,
            "output_dir": str(output_dir),
            "has_previous_result": bool(existing and (output_dir / "summary.json").is_file()),
        }

    def create_job(self, source_path: str) -> dict[str, object]:
        active = self.db.get_active_job()
        if active:
            raise RuntimeError(f"当前已有任务正在执行：{active['id']}")
        descriptor = self.describe_source(source_path)
        source = Path(str(descriptor["source_path"]))
        system_id = str(descriptor["system_id"])
        output_dir = Path(str(descriptor["output_dir"]))
        self.db.upsert_system(
            system_id=system_id,
            source_path=str(source),
            display_name=source.name,
            department=source.parent.name,
            output_dir=str(output_dir),
        )
        job_id = uuid.uuid4().hex
        events = event_path(job_id)
        events.parent.mkdir(parents=True, exist_ok=True)
        if events.exists():
            events.unlink()
        job = self.db.create_job(
            {
                "id": job_id,
                "system_id": system_id,
                "source_path": str(source),
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "message": "等待工作进程启动",
            }
        )
        worker_log = STATE_ROOT / "jobs" / job_id / "worker.log"
        log_stream = worker_log.open("a", encoding="utf-8")
        command = [
            sys.executable,
            "-m",
            "platform_app.worker",
            "--job-id",
            job_id,
            "--database",
            str(DATABASE_PATH),
        ]
        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(PROJECT_ROOT)
        try:
            process = subprocess.Popen(
                command,
                cwd=PROJECT_ROOT,
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=environment,
            )
        except Exception:
            log_stream.close()
            self.db.update_job(job_id, status="failed", stage="failed", message="无法启动工作进程")
            raise
        log_stream.close()
        self._processes[job_id] = process
        threading.Thread(
            target=self._reap_process,
            args=(job_id, process),
            name=f"audit-worker-{job_id[:8]}",
            daemon=True,
        ).start()
        job = self.db.update_job(job_id, pid=process.pid, message="工作进程已启动")
        append_event(job_id, {"type": "queued", "stage": "queued", "message": "任务已创建"})
        return job

    def cancel_job(self, job_id: str) -> dict[str, object]:
        job = self.db.get_job(job_id)
        if job is None:
            raise KeyError(f"任务不存在：{job_id}")
        if str(job.get("status")) not in ACTIVE_STATUSES:
            return job
        self.db.update_job(job_id, status="cancelling", message="正在取消任务")
        pid = job.get("pid")
        if pid:
            try:
                os.killpg(int(pid), signal.SIGTERM)
            except ProcessLookupError:
                pass
        append_event(job_id, {"type": "job_cancelled", "stage": "cancelled", "message": "任务已取消，上一次有效结果保持不变"})
        return self.db.update_job(job_id, status="cancelled", stage="cancelled", message="任务已取消", error=None)
