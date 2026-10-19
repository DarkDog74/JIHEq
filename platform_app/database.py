from __future__ import annotations

import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
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
        # Back up the pre-auth schema before its first migration.
        with closing(sqlite3.connect(self.path)) as check:
            columns = {row[1] for row in check.execute("PRAGMA table_info(systems)")}
            if columns and "owner_user_id" not in columns:
                backup = self.path.with_name(self.path.name + ".pre-auth.bak")
                if not backup.exists():
                    with closing(sqlite3.connect(backup)) as destination:
                        check.backup(destination)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL, role TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                csrf_token TEXT NOT NULL, expires_at REAL NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS login_attempts (
                key TEXT PRIMARY KEY, count INTEGER NOT NULL, expires_at REAL NOT NULL)""")
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS systems (
                    id TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    owner_user_id TEXT,
                    display_name TEXT NOT NULL,
                    department TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(owner_user_id, source_path)
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
            columns = {row[1] for row in db.execute("PRAGMA table_info(systems)")}
            if "owner_user_id" not in columns:
                db.execute("""CREATE TABLE systems_auth (
                    id TEXT PRIMARY KEY, source_path TEXT NOT NULL,
                    owner_user_id TEXT, display_name TEXT NOT NULL,
                    department TEXT NOT NULL, output_dir TEXT NOT NULL,
                    updated_at TEXT NOT NULL, UNIQUE(owner_user_id, source_path))""")
                db.execute("""INSERT INTO systems_auth
                    (id, source_path, display_name, department, output_dir, updated_at)
                    SELECT id, source_path, display_name, department, output_dir, updated_at FROM systems""")
                db.execute("DROP TABLE systems")
                db.execute("ALTER TABLE systems_auth RENAME TO systems")
            if "owner_user_id" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}:
                db.execute("ALTER TABLE jobs ADD COLUMN owner_user_id TEXT")
            db.execute("CREATE INDEX IF NOT EXISTS idx_system_owner ON systems(owner_user_id)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_job_owner ON jobs(owner_user_id)")
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
        owner_user_id: str | None = None,
    ) -> dict[str, object]:
        timestamp = now_iso()
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO systems(id, source_path, display_name, department, output_dir, updated_at, owner_user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    source_path=excluded.source_path,
                    display_name=excluded.display_name,
                    department=excluded.department,
                    output_dir=excluded.output_dir,
                    updated_at=excluded.updated_at
                """,
                (system_id, source_path, display_name, department, output_dir, timestamp, owner_user_id),
            )
            row = db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone()
        return dict(row)

    def list_systems(self, limit: int = 12, *, owner_user_id: str | None = None) -> list[dict[str, object]]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT * FROM systems " + ("WHERE owner_user_id = ? " if owner_user_id else "")
                + "ORDER BY updated_at DESC LIMIT ?",
                (owner_user_id, limit) if owner_user_id else (limit,)
            ).fetchall()
        return [dict(row) for row in rows]

    def get_system(self, system_id: str) -> dict[str, object] | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM systems WHERE id = ?", (system_id,)).fetchone())

    def create_job(self, job: dict[str, object]) -> dict[str, object]:
        timestamp = now_iso()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone():
                raise RuntimeError("当前服务正在处理任务，请稍后重试")
            db.execute(
                """
                INSERT INTO jobs(
                    id, system_id, source_path, status, stage, progress, message,
                    pid, result_path, error, created_at, updated_at, owner_user_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["id"], job["system_id"], job["source_path"],
                    job.get("status", "queued"), job.get("stage", "queued"),
                    job.get("progress", 0), job.get("message", "等待执行"),
                    job.get("pid"), job.get("result_path"), job.get("error"),
                    timestamp, timestamp, job.get("owner_user_id"),
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

    def create_user(self, username: str, password_hash: str, role: str = "user") -> dict:
        username = username.strip().casefold()
        if not username or len(username) > 64 or role not in {"user", "admin"}:
            raise ValueError("账号或角色无效")
        user_id = uuid.uuid4().hex
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            first = not db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
            if first and role != "admin":
                raise ValueError("首个账号必须为管理员")
            db.execute("INSERT INTO users VALUES (?, ?, ?, ?, 1, ?)",
                       (user_id, username, password_hash, role, now_iso()))
            if first:
                db.execute("UPDATE systems SET owner_user_id=? WHERE owner_user_id IS NULL", (user_id,))
                db.execute("UPDATE jobs SET owner_user_id=? WHERE owner_user_id IS NULL", (user_id,))
        return self.get_user(username)

    def get_user(self, username: str) -> dict | None:
        with self.connect() as db:
            return self._row(db.execute("SELECT * FROM users WHERE username=?", (username.strip().casefold(),)).fetchone())

    def set_user(self, username: str, *, password_hash: str | None = None, enabled: bool | None = None) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE username=?", (username.strip().casefold(),)).fetchone()
            if not user:
                raise ValueError("账号不存在")
            if enabled is False and user["role"] == "admin" and user["enabled"]:
                count = db.execute("SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0]
                if count <= 1:
                    raise ValueError("不能禁用最后一个管理员")
            if password_hash is not None:
                db.execute("UPDATE users SET password_hash=? WHERE id=?", (password_hash, user["id"]))
            if enabled is not None:
                db.execute("UPDATE users SET enabled=? WHERE id=?", (int(enabled), user["id"]))
            db.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))

    def new_session(self, digest: str, user_id: str, csrf: str, expires_at: float,
                    *, expected_password_hash: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            # A concurrent reset/disable must not mint a session for old credentials.
            user = db.execute("SELECT password_hash, enabled FROM users WHERE id=?", (user_id,)).fetchone()
            if not user or not user["enabled"] or user["password_hash"] != expected_password_hash:
                raise ValueError("账号状态已变化，请重新登录")
            db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)", (digest, user_id, csrf, expires_at))

    def get_session(self, digest: str) -> dict | None:
        with self.connect() as db:
            return self._row(db.execute("""SELECT u.id, u.username, u.role, s.csrf_token, s.expires_at
                FROM sessions s JOIN users u ON u.id=s.user_id
                WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1""", (digest, time.time())).fetchone())

    def delete_session(self, digest: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))

    def allow_login_attempt(self, key: str, limit: int = 10) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE expires_at<=?", (time.time(),))
            row = db.execute("SELECT count FROM login_attempts WHERE key=?", (key,)).fetchone()
            if row and row[0] >= limit:
                return False
            db.execute("""INSERT INTO login_attempts VALUES (?, 1, ?)
                ON CONFLICT(key) DO UPDATE SET count=count+1""", (key, time.time()+900))
        return True
