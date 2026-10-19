from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from platform_app import api
from platform_app.auth import COOKIE_NAME, hash_password, token_hash, verify_password
from platform_app.database import Database
from platform_app.job_manager import JobManager, system_id_for


class AuthIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = hash_password("a-safe-test-password")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = Database(self.root / "platform.db")
        self.admin = self.db.create_user("admin", self.encoded, "admin")
        self.alice = self.db.create_user("alice", self.encoded)
        self.bob = self.db.create_user("bob", self.encoded)
        for target, value in (("db", self.db), ("UPLOAD_ROOT", self.root / "uploads"),
                              ("jobs", JobManager(self.db))):
            patcher = patch.object(api, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        patcher = patch.dict(os.environ, {"AUDIT_COOKIE_SECURE": "false"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.a = TestClient(api.app)
        self.b = TestClient(api.app)
        self.addCleanup(self.a.close)
        self.addCleanup(self.b.close)

    def login(self, client, username="alice"):
        response = client.post("/api/auth/login", json={"username": username, "password": "a-safe-test-password"})
        self.assertEqual(response.status_code, 200, response.text)
        client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
        return response

    def seed_result(self):
        output = self.root / "alice-results"
        output.mkdir()
        self.db.upsert_system(system_id="alice-system", source_path="/alice/source", display_name="Alice",
                              department="Dept", output_dir=str(output), owner_user_id=self.alice["id"])
        self.db.create_job({"id": "alice-job", "system_id": "alice-system", "source_path": "/alice/source",
                            "owner_user_id": self.alice["id"], "status": "completed"})
        summary = {"system": {"id": "alice-system", "name": "Alice", "department": "Dept"},
                   "parse": {"file_count": 1}, "audit": {"items": [], "counts": {}, "item_count": 0}}
        (output / "summary.json").write_text(json.dumps(summary), encoding="utf-8")

    def test_unauthenticated_endpoints_reject(self):
        for method, path in [("get", "/api/systems"), ("get", "/api/auth/me"),
                             ("get", "/api/jobs/secret/events"), ("get", "/api/systems/secret/report"),
                             ("post", "/api/jobs"), ("post", "/api/systems/upload?folder=x&relative_path=x/a.docx")]:
            self.assertEqual(getattr(self.a, method)(path).status_code, 401, path)
        self.assertNotIn("ai", self.a.get("/api/health").json())

    def test_login_cookie_and_session_are_private(self):
        response = self.login(self.a)
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=strict", cookie)
        self.assertNotIn("password_hash", response.text)
        with self.db.connect() as db:
            digest = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
        self.assertNotEqual(digest, self.a.cookies[COOKIE_NAME])
        self.assertEqual(self.a.get("/api/auth/me").json()["user"]["username"], "alice")

    def test_secure_cookie_default(self):
        with patch.dict(os.environ, {"AUDIT_COOKIE_SECURE": "true"}):
            response = self.a.post("/api/auth/login", json={"username": "alice", "password": "a-safe-test-password"})
        self.assertIn("Secure", response.headers["set-cookie"])

    def test_csrf_and_cross_origin_are_rejected(self):
        self.login(self.a)
        self.assertEqual(self.a.post("/api/auth/logout", headers={"X-CSRF-Token": "bad"}).status_code, 403)
        self.assertEqual(self.a.post("/api/auth/logout", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.a.post("/api/auth/login", json={"username": "alice", "password": "a-safe-test-password"},
                                     headers={"Origin": "https://evil.example"}).status_code, 403)

    def test_logout_revokes_stolen_cookie(self):
        self.login(self.a)
        old = self.a.cookies[COOKIE_NAME]
        self.assertEqual(self.a.post("/api/auth/logout").status_code, 200)
        self.b.cookies.set(COOKIE_NAME, old)
        self.assertEqual(self.b.get("/api/auth/me").status_code, 401)

    def test_expiry_disable_and_password_reset_revoke_sessions(self):
        self.login(self.a)
        with self.db.connect() as db:
            db.execute("UPDATE sessions SET expires_at=?", (time.time()-1,))
        self.assertEqual(self.a.get("/api/auth/me").status_code, 401)
        self.login(self.a)
        self.db.set_user("alice", enabled=False)
        self.assertEqual(self.a.get("/api/auth/me").status_code, 401)
        self.db.set_user("alice", enabled=True)
        self.login(self.a)
        self.db.set_user("alice", password_hash=self.encoded)
        self.assertEqual(self.a.get("/api/auth/me").status_code, 401)

    def test_login_throttling_and_generic_error(self):
        for username in ["missing", "alice"]:
            response = self.a.post("/api/auth/login", json={"username": username, "password": "wrong"})
            self.assertEqual(response.json()["detail"], "账号或密码错误")
        for _ in range(9):
            response = self.a.post("/api/auth/login", json={"username": "missing", "password": "wrong"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.a.post("/api/auth/login", json={"username": "missing", "password": "wrong"}).status_code, 429)

    def test_identical_upload_names_are_isolated(self):
        self.login(self.a)
        self.login(self.b, "bob")
        endpoint = "/api/systems/upload?folder=same&relative_path=same/定级报告.docx"
        self.assertEqual(self.a.post(endpoint, content=b"alice").status_code, 200)
        self.assertEqual(self.b.post(endpoint, content=b"bob").status_code, 200)
        uploads = self.root / "uploads"
        self.assertEqual((uploads / self.alice["id"] / "same/定级报告.docx").read_bytes(), b"alice")
        self.assertEqual((uploads / self.bob["id"] / "same/定级报告.docx").read_bytes(), b"bob")
        response = self.a.post("/api/systems/upload/finish", json={"folder": "same"})
        self.assertEqual(response.status_code, 200, response.text)
        source = response.json()["source_path"]
        self.assertEqual(self.a.post("/api/systems/validate", json={"source_path": source}).status_code, 200)
        self.assertEqual(self.b.post("/api/systems/validate", json={"source_path": source}).status_code, 403)
        self.assertEqual(self.b.post("/api/jobs", json={"source_path": source}).status_code, 403)

    def test_path_traversal_and_server_picker_rejected(self):
        self.login(self.a)
        self.assertEqual(self.a.post("/api/systems/upload?folder=x&relative_path=../secret", content=b"x").status_code, 400)
        self.assertEqual(self.a.get("/api/systems/browse", params={"path": str(self.root)}).status_code, 403)
        self.assertEqual(self.a.post("/api/systems/select-folder").status_code, 403)

    def test_link_escape_is_rejected(self):
        self.login(self.a)
        home = self.root / "uploads" / self.alice["id"] / "material"
        home.mkdir(parents=True)
        try:
            (home / "other").symlink_to(self.root, target_is_directory=True)
        except OSError:
            self.skipTest("Windows requires symlink privileges")
        self.assertEqual(self.a.post("/api/systems/validate", json={"source_path": str(home)}).status_code, 403)

    def test_all_other_user_resource_endpoints_reject_before_side_effects(self):
        self.seed_result()
        self.login(self.a)
        self.login(self.b, "bob")
        self.assertEqual(len(self.a.get("/api/systems").json()), 1)
        self.assertEqual(self.b.get("/api/systems").json(), [])
        paths = ["/api/jobs/alice-job", "/api/jobs/alice-job/events", "/api/systems/alice-system/summary",
                 "/api/systems/alice-system/audits/DJ-02", "/api/systems/alice-system/report"]
        with patch.object(api.jobs, "get_job", side_effect=AssertionError("must check ownership first")):
            for path in paths:
                self.assertEqual(self.b.get(path).status_code, 404, path)
        self.assertEqual(self.b.post("/api/jobs/alice-job/cancel").status_code, 404)
        self.assertEqual(self.a.get("/api/systems/alice-system/summary").status_code, 200)
        self.assertEqual(self.db.get_job("alice-job")["status"], "completed")

    def test_owner_can_download_report_and_read_event_stream(self):
        self.seed_result()
        self.login(self.a)
        report = self.a.get("/api/systems/alice-system/report")
        self.assertEqual(report.status_code, 200)
        self.assertIn("attachment", report.headers["content-disposition"])
        self.assertIn("Alice", report.text)
        events = self.root / "events.jsonl"
        events.write_text('{"type":"completed"}\n', encoding="utf-8")
        with patch.object(api, "event_path", return_value=events):
            response = self.a.get("/api/jobs/alice-job/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("event: end", response.text)

    def test_prefix_routes_keep_auth_and_cookie_scope(self):
        with patch.object(api, "_URL_PREFIX", "/audit"):
            client = TestClient(api._UrlPrefixMiddleware(api.app))
            self.addCleanup(client.close)
            response = self.login(client)
            self.assertIn("Path=/audit/", response.headers["set-cookie"])
            self.assertEqual(client.get("/audit/api/auth/me").status_code, 200)
            # The root path does not receive the prefix-scoped cookie.
            self.assertEqual(client.get("/api/auth/me").status_code, 401)

    def test_admin_does_not_implicitly_read_other_user_data(self):
        self.seed_result()
        self.login(self.a, "admin")
        self.assertEqual(self.a.get("/api/systems/alice-system/summary").status_code, 404)

    def test_model_configuration_is_admin_only(self):
        self.login(self.a)
        for method, path in [("get", "/api/model-config"), ("put", "/api/model-config"), ("post", "/api/model-config/test")]:
            self.assertEqual(getattr(self.a, method)(path).status_code, 403)
        self.login(self.b, "admin")
        self.assertEqual(self.b.get("/api/model-config").status_code, 200)

    def test_owner_written_to_new_jobs_and_output_namespace(self):
        self.login(self.a)
        source = self.root / "uploads" / self.alice["id"] / "system"
        source.mkdir(parents=True)
        (source / "定级报告.docx").write_bytes(b"word")
        # Fail after atomic task reservation, without launching a real parser.
        with patch("platform_app.job_manager.subprocess.Popen", side_effect=OSError("test launch")):
            with self.assertRaises(OSError):
                api.jobs.create_job(str(source), owner_user_id=self.alice["id"])
        systems = self.db.list_systems(owner_user_id=self.alice["id"])
        self.assertIn(self.alice["id"], systems[0]["output_dir"])
        job = self.db.latest_job_for_system(systems[0]["id"])
        self.assertEqual(job["owner_user_id"], self.alice["id"])
        self.assertEqual(job["status"], "failed")
        self.assertNotEqual(system_id_for(source, self.alice["id"]), system_id_for(source, self.bob["id"]))

    def test_database_reservation_is_atomic(self):
        def reserve(index):
            try:
                self.db.create_job({"id": str(index), "system_id": "s", "source_path": "/source"})
                return True
            except RuntimeError as error:
                self.assertNotIn("alice", str(error))
                return False
        with ThreadPoolExecutor(max_workers=6) as pool:
            self.assertEqual(sum(pool.map(reserve, range(6))), 1)

    def test_sse_stops_when_session_revoked(self):
        self.login(self.a)
        cookie = self.a.cookies[COOKIE_NAME]
        self.db.delete_session(token_hash(cookie))
        from starlette.requests import Request
        request = Request({"type": "http", "headers": [(b"cookie", f"{COOKIE_NAME}={cookie}".encode())]})
        async def consume():
            with patch.object(api, "_job_or_404", return_value={}):
                response = await api.stream_events("job", "0", request)
            return "".join([chunk async for chunk in response.body_iterator])
        self.assertIn("auth_expired", asyncio.run(consume()))

    def test_session_survives_database_reopen(self):
        self.login(self.a)
        other = Database(self.db.path)
        self.assertEqual(other.get_session(token_hash(self.a.cookies[COOKIE_NAME]))["id"], self.alice["id"])

    def test_password_reset_during_login_prevents_session_creation(self):
        original_verify = verify_password
        def reset_after_verification(encoded, password):
            valid = original_verify(encoded, password)
            self.db.set_user("alice", password_hash=hash_password("a-different-test-password"))
            return valid
        with patch.object(api, "verify_password", side_effect=reset_after_verification):
            response = self.a.post("/api/auth/login", json={"username": "alice", "password": "a-safe-test-password"})
        self.assertEqual(response.status_code, 401)
        with self.db.connect() as db:
            self.assertEqual(db.execute("SELECT count(*) FROM sessions").fetchone()[0], 0)


class AuthMigrationTests(unittest.TestCase):
    def test_legacy_schema_is_backed_up_and_claimed_by_first_admin(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "legacy.db"
            with closing(sqlite3.connect(path)) as db:
                db.execute("""CREATE TABLE systems (id TEXT PRIMARY KEY, source_path TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, department TEXT NOT NULL, output_dir TEXT NOT NULL, updated_at TEXT NOT NULL)""")
                db.execute("INSERT INTO systems VALUES ('old','/source','Old','Dept','/output','now')")
                db.commit()
            database = Database(path)
            self.assertTrue(path.with_name("legacy.db.pre-auth.bak").is_file())
            self.assertIsNone(database.get_system("old")["owner_user_id"])
            admin = database.create_user("admin", hash_password("a-safe-test-password"), "admin")
            self.assertEqual(database.get_system("old")["owner_user_id"], admin["id"])
            database.upsert_system(system_id="other", source_path="/source", display_name="Other",
                                   department="Dept", output_dir="/other", owner_user_id="other-user")
            self.assertEqual(len(database.list_systems()), 2)
            Database(path)
            self.assertEqual(len(database.list_systems()), 2)

    def test_password_hash_and_first_admin_requirement(self):
        encoded = hash_password("a-safe-test-password")
        self.assertTrue(verify_password(encoded, "a-safe-test-password"))
        self.assertFalse(verify_password(encoded, "incorrect"))
        with self.assertRaises(ValueError):
            hash_password("short")
        with tempfile.TemporaryDirectory() as temporary:
            database = Database(Path(temporary) / "db")
            with self.assertRaises(ValueError):
                database.create_user("user", encoded)
            database.create_user("admin", encoded, "admin")
            with self.assertRaises(ValueError):
                database.set_user("admin", enabled=False)
