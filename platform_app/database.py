from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

from .config import DATABASE_PATH, ensure_storage


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path = DATABASE_PATH) -> None:
        self.path = Path(path)
        if self.path.resolve(strict=False) == DATABASE_PATH.resolve(strict=False):
            ensure_storage()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS systems (
                    id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    system_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0,
                    message TEXT NOT NULL DEFAULT '',
                    pid INTEGER,
                    result_path TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(system_id) REFERENCES systems(id)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status)"
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_jobs_system_updated ON jobs(system_id, updated_at)"
            )
            db.execute("PRAGMA optimize")

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, object] | None:
        return dict(row) if row is not None else None

    def upsert_system(
        self,
        *,
        system_id: str,
        source_path: str,
        display_name: str,
        department: str,
        output_dir: str,
    ) -> dict[str, object]:
        timestamp = now_iso()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO systems(id, source_path, display_name, department, output_dir, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_path=excluded.source_path,
                    display_name=excluded.display_name,
                    department=excluded.department,
                    output_dir=excluded.output_dir,
                    updated_at=excluded.updated_at
                """,
                (system_id, source_path, display_name, department, output_dir, timestamp),
            )
            row = db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
        return dict(row)

    def list_systems(self, limit: int = 12) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM systems ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_system(self, system_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone())

    def create_job(self, job: dict[str, object]) -> dict[str, object]:
        timestamp = now_iso()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO jobs(
                    id, system_id, source_path, status, stage, progress, message,
                    pid, result_path, error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"], job["system_id"], job["source_path"],
                    job.get("status", "queued"), job.get("stage", "queued"),
                    job.get("progress", 0), job.get("message", "等待执行"),
                    job.get("pid"), job.get("result_path"), job.get("error"),
                    timestamp, timestamp,
                ),
            )
        return self.get_job(str(job["id"])) or job

    def update_job(self, job_id: str, **values: object) -> dict[str, object]:
        if not values:
            return self.get_job(job_id) or {}
        allowed = {"status", "stage", "progress", "message", "pid", "result_path", "error"}
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"未知任务字段：{sorted(unknown)}")
        values["updated_at"] = now_iso()
        assignments = ", ".join(f"{key} = ?" for key in values)
        parameters = [*values.values(), job_id]
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", parameters)
        return self.get_job(job_id) or {}

    def get_job(self, job_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def get_active_job(self) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE status IN ('queued', 'running', 'cancelling') ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return self._row(row)

    def latest_job_for_system(self, system_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM jobs WHERE system_id = ? ORDER BY updated_at DESC LIMIT 1",
                (system_id,),
            ).fetchone()
        return self._row(row)
