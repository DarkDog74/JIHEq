from __future__ import annotations

import json
import unittest
from unittest.mock import Mock, patch

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    ModelReply,
    chat,
    extract_json_object,
)
from audit_rules.model_support import (
    confidence_of,
    extract_node_ips,
    extract_node_names,
)


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake-model", elapsed_ms=3
    )


class ExtractJsonObjectTests(unittest.TestCase):
    def test_extracts_plain_and_prose_wrapped(self) -> None:
        self.assertEqual(extract_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(
            extract_json_object('结论如下：\n{"a": {"b": 2}} 以上。'), {"a": {"b": 2}}
        )

    def test_skips_broken_block_and_finds_next(self) -> None:
        text = '前缀 {"broken": } 中间 {"ok": true} 后缀'
        self.assertEqual(extract_json_object(text), {"ok": True})

    def test_raises_when_no_json(self) -> None:
        with self.assertRaises(ModelCallError):
            extract_json_object("完全没有 JSON 的回复")

    def test_unbalanced_braces_inside_string_value(self) -> None:
        text = '{"片段": "右括号 } 未配对", "代码": "if (a) {{ 开两层", "ok": true}'
        self.assertEqual(
            extract_json_object(text),
            {"片段": "右括号 } 未配对", "代码": "if (a) {{ 开两层", "ok": True},
        )

    def test_escaped_quotes_inside_string_value(self) -> None:
        text = '{"文本": "他说\\"{你好}\\"", "ok": true}'
        self.assertEqual(
            extract_json_object(text), {"文本": '他说"{你好}"', "ok": True}
        )

    def test_fenced_json_block_fallback(self) -> None:
        text = '说明 { 未闭合的片段\n```json\n{"ok": true}\n```\n以上。'
        self.assertEqual(extract_json_object(text), {"ok": True})

    def test_invalid_fenced_block_still_raises(self) -> None:
        with self.assertRaises(ModelCallError):
            extract_json_object('```json\n{不是JSON}\n```')

    def test_odd_bare_quotes_before_json_falls_back_to_legacy_scan(self) -> None:
        # JSON 前出现奇数个裸引号：字符串状态扫描会整体卡进字符串态，
        # 围栏也没有——由不带字符串状态的旧版配平扫描兜底（两版行为取并集）
        text = '拓扑识别结果为"高可信，具体如下：{"节点": [], "置信度": 0.9}'
        self.assertEqual(extract_json_object(text), {"节点": [], "置信度": 0.9})


class ChatConfigurationTests(unittest.TestCase):
    def test_chat_raises_when_not_configured(self) -> None:
        empty = {
            "base_url": "",
            "api_key": "",
            "model": "",
            "timeout_seconds": 30,
        }
        with patch("core.model_client.load_model_config", return_value=empty):
            with self.assertRaises(ModelNotConfiguredError):
                chat("ping")

    def test_glm_53_flash_uses_supported_low_thinking_mode(self) -> None:
        config = {
            "base_url": "https://model.example/v1",
            "api_key": "test-key",
            "model": "glm-5.3-flash",
            "timeout_seconds": 30,
        }
        response = Mock(
            status_code=200,
            text='{"choices":[{"message":{"content":"OK"}}]}',
            headers={},
        )
        response.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        with (
            patch("core.model_client.load_model_config", return_value=config),
            patch("core.model_client.httpx.post", return_value=response) as post,
        ):
            reply = chat("ping", max_tokens=128)

        self.assertEqual(reply.text, "OK")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(
            payload["thinking"], {"type": "enabled", "clear_thinking": False}
        )
        self.assertEqual(payload["reasoning_effort"], "low")


class RetryPolicyTests(unittest.TestCase):
    """确定性 4xx 立即失败不重试；429/5xx 维持现有重试与退避。"""

    def _config(self) -> dict:
        return {
            "base_url": "http://model.invalid",
            "api_key": "test-key",
            "model": "glm-5v-turbo",
            "timeout_seconds": 30,
        }

    def test_401_fails_immediately_without_retry(self) -> None:
        response = Mock(status_code=401, text="Unauthorized", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep") as sleep,
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 1)
        sleep.assert_not_called()
        self.assertIn("HTTP 401", str(context.exception))

    def test_429_still_retries_until_limit(self) -> None:
        response = Mock(status_code=429, text="", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep"),
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 4)
        self.assertIn("429", str(context.exception))

    def test_500_still_retries_until_limit(self) -> None:
        response = Mock(status_code=500, text="server error", headers={})
        with (
            patch("core.model_client.load_model_config", return_value=self._config()),
            patch("core.model_client.httpx.post", return_value=response) as post,
            patch("core.model_client.time.sleep"),
        ):
            with self.assertRaises(ModelCallError) as context:
                chat("ping")
        self.assertEqual(post.call_count, 4)
        self.assertIn("HTTP 500", str(context.exception))


class ModelSupportTests(unittest.TestCase):
    def test_confidence_of_variants(self) -> None:
        self.assertEqual(confidence_of({"置信度": 0.8}), 0.8)
        self.assertEqual(confidence_of({"置信度": "0.9"}), 0.9)
        self.assertIsNone(confidence_of({"置信度": None}))
        self.assertIsNone(confidence_of({}))

    def test_extract_node_ips_filters_invalid(self) -> None:
        payload = {
            "节点": [
                {"名称": "a", "IP": "10.0.0.1"},
                {"名称": "b", "IP": "不是IP"},
                {"名称": "c", "IP": None},
                {"名称": "d", "IP": "10.0.0.1"},
            ]
        }
        self.assertEqual(extract_node_ips(payload), ["10.0.0.1"])
        self.assertEqual(extract_node_names(payload), ["a", "b", "c", "d"])


if __name__ == "__main__":
    unittest.main()
