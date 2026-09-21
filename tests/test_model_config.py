from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from platform_app.model_config import (
    load_model_config,
    mask_api_key,
    normalize_config,
    public_view,
    save_model_config,
    probe_model_connection,
)


class NormalizeConfigTests(unittest.TestCase):
    def test_strips_trailing_slash_and_whitespace(self) -> None:
        normalized = normalize_config(
            {"base_url": " https://api.example.com/v1/ ", "model": " glm-5.3-flash "}
        )
        self.assertEqual(normalized["base_url"], "https://api.example.com/v1")
        self.assertEqual(normalized["model"], "glm-5.3-flash")

    def test_rejects_unsafe_model_identifier(self) -> None:
        with self.assertRaisesRegex(ValueError, "模型编码"):
            normalize_config({"model": "bad model; rm"})

    def test_clamps_timeout_into_bounds(self) -> None:
        self.assertEqual(normalize_config({"timeout_seconds": 999})["timeout_seconds"], 300)
        self.assertEqual(normalize_config({"timeout_seconds": 1})["timeout_seconds"], 3)
        self.assertEqual(
            normalize_config({"timeout_seconds": "not-a-number"})["timeout_seconds"], 120
        )


class SaveLoadConfigTests(unittest.TestCase):
    def test_roundtrip_and_masked_public_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            view = save_model_config(
                {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-test1234567890abcd",
                    "model": "glm-5.3-flash",
                    "timeout_seconds": 45,
                },
                path=path,
            )
            self.assertTrue(view["api_key_set"])
            self.assertEqual(view["api_key_masked"], "sk-••••abcd")
            self.assertNotIn("api_key", view)
            self.assertTrue(view["configured"])

            stored = load_model_config(path)
            self.assertEqual(stored["api_key"], "sk-test1234567890abcd")
            self.assertEqual(stored["model"], "glm-5.3-flash")
            self.assertEqual(stored["timeout_seconds"], 45)

    def test_empty_api_key_keeps_saved_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            save_model_config(
                {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "sk-keep-me-0000",
                    "model": "glm-5.3-flash",
                },
                path=path,
            )
            view = save_model_config({"api_key": ""}, path=path)
            self.assertTrue(view["api_key_set"])
            self.assertEqual(load_model_config(path)["api_key"], "sk-keep-me-0000")
            self.assertEqual(load_model_config(path)["model"], "glm-5.3-flash")

    def test_saved_file_is_owner_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            save_model_config(
                {"base_url": "https://api.example.com/v1", "api_key": "sk-secret-0000"},
                path=path,
            )
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)

    def test_missing_file_returns_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = load_model_config(Path(directory) / "model_config.json")
        self.assertEqual(config["base_url"], "https://open.bigmodel.cn/api/paas/v4")
        self.assertEqual(config["model"], "glm-5v-turbo")
        self.assertEqual(config["timeout_seconds"], 120)

    def test_legacy_dual_model_key_is_not_migrated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            path.write_text(
                '{"base_url":"https://old.example/v1","api_key":"old-exposed-key",'
                '"text_model":"glm-5.3","vision_model":"glm-4v"}',
                encoding="utf-8",
            )
            config = load_model_config(path)

        self.assertEqual(config["api_key"], "")
        self.assertEqual(config["model"], "glm-5v-turbo")


class MaskApiKeyTests(unittest.TestCase):
    def test_masks_long_and_short_keys(self) -> None:
        self.assertEqual(mask_api_key("sk-abcdefgh1234"), "sk-••••1234")
        self.assertEqual(mask_api_key("short"), "••••")
        self.assertEqual(mask_api_key(""), "")


class PublicViewTests(unittest.TestCase):
    def test_never_leaks_plaintext_key(self) -> None:
        serialized = str(
            public_view({"api_key": "sk-super-secret-value-9999", "base_url": "x"})
        )
        self.assertNotIn("sk-super-secret-value-9999", serialized)

    def test_returns_current_model_and_suggestions(self) -> None:
        view = public_view(
            {
                "api_key": "key",
                "base_url": "https://api.example.com/v1",
                "model": "glm-5.3-flash",
            }
        )
        self.assertEqual(view["model"], "glm-5.3-flash")
        self.assertIn("glm-5v-turbo", view["suggested_models"])
        self.assertIn("glm-5.3-flash", view["suggested_models"])


class TestConnectionValidationTests(unittest.TestCase):
    """只测本地校验分支，不发任何网络请求。"""

    def test_default_base_url_still_requires_key(self) -> None:
        result = probe_model_connection({"base_url": "", "api_key": ""})
        self.assertFalse(result["ok"])
        self.assertIn("API Key", result["message"])

    def test_rejects_url_without_scheme(self) -> None:
        result = probe_model_connection(
            {"base_url": "api.example.com/v1", "api_key": "key"}
        )
        self.assertFalse(result["ok"])
        self.assertIn("http", result["message"])

    def test_requires_api_key(self) -> None:
        result = probe_model_connection(
            {"base_url": "https://api.example.com/v1", "api_key": ""}
        )
        self.assertFalse(result["ok"])
        self.assertIn("API Key", result["message"])

    def test_connection_probe_uses_selected_model(self) -> None:
        response = Mock(status_code=200)
        response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}]
        }
        with patch("platform_app.model_config.httpx.post", return_value=response) as post:
            result = probe_model_connection(
                {
                    "base_url": "https://api.example.com/v1",
                    "api_key": "key",
                    "model": "glm-5.3-flash",
                }
            )

        self.assertTrue(result["ok"])
        self.assertEqual(result["model"], "glm-5.3-flash")
        self.assertEqual(post.call_args.kwargs["json"]["model"], "glm-5.3-flash")


class ConfigPermissionAndConcurrencyTests(unittest.TestCase):
    PAYLOAD = {
        "base_url": "https://api.example.com/v1",
        "api_key": "sk-test1234567890abcd",
        "model": "glm-5v-turbo",
        "timeout_seconds": 45,
    }

    def test_load_tightens_overwide_existing_file(self) -> None:
        # 升级部署前遗留的 0644 配置文件：读取时顺带收紧为 0600
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            path.write_text(
                '{"base_url": "https://api.example.com/v1", "api_key": "k"}',
                encoding="utf-8",
            )
            os.chmod(path, 0o644)

            load_model_config(path)

            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_concurrent_saves_do_not_collide(self) -> None:
        # 并发保存（保存 + 测试连接同时点击）使用各自的临时文件，互不 replace
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model_config.json"
            errors: list[Exception] = []

            def save_many() -> None:
                try:
                    for _ in range(40):
                        save_model_config(self.PAYLOAD, path=path)
                except Exception as error:  # noqa: BLE001 - 记录后统一断言
                    errors.append(error)

            threads = [threading.Thread(target=save_many) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
