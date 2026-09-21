from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx

from extractors import RequiredDocumentNotFoundError
from platform_app.ai_service import (
    AuditAIService,
    build_request_payload,
    compact_fields,
    serialize_request,
)
from platform_app.audit_service import run_unit_audits
from platform_app.config import AISettings, ai_config_status, load_ai_settings


def _settings(**overrides: object) -> AISettings:
    values: dict[str, object] = {
        "enabled": True,
        "api_key": "unit-test-key",
        "endpoint": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "model": "glm-5v-turbo",
        "timeout_seconds": 10.0,
        "max_tokens": 1024,
        "temperature": 0.2,
        "max_input_bytes": 32 * 1024,
    }
    values.update(overrides)
    return AISettings(**values)


def _item_payload(item_dir: Path, *, fields: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "item_id": "DJ-02",
        "item_name": "检查定级结果",
        "current": 1,
        "total": 2,
        "fields": fields or {"社会影响力赋值": 2},
        "rule_result": {
            "passed": True,
            "message": "定级结果一致",
            "issues": [],
        },
        "normalized": {
            "status": "passed",
            "message": "定级结果一致",
            "issues": [],
            "extracted_summary": [{"field": "社会影响力赋值", "value": "2"}],
        },
        "item_dir": item_dir,
    }


class AIConfigurationTests(unittest.TestCase):
    def test_web_model_config_is_the_only_active_secret_source(self) -> None:
        web_config = {
            "base_url": "https://open.bigmodel.cn/api/paas/v4",
            "api_key": "web-config-key",
            "model": "glm-5.3-flash",
            "timeout_seconds": 120,
        }
        with patch("platform_app.model_config.load_model_config", return_value=web_config):
            settings = load_ai_settings(
                environment={"AUDIT_LLM_ENABLED": "true", "AUDIT_LLM_API_KEY": "ignored"},
                secrets_path=Path("/missing/secrets.env"),
            )

        self.assertTrue(settings.enabled)
        self.assertEqual(settings.api_key, "web-config-key")
        self.assertEqual(settings.model, "glm-5.3-flash")
        status = ai_config_status(settings)
        self.assertTrue(status["configured"])
        self.assertEqual(status["endpoint_host"], "open.bigmodel.cn")
        self.assertNotIn("api_key", status)
        self.assertNotIn("web-config-key", repr(status))

    def test_default_output_limit_allows_a_complete_natural_explanation(self) -> None:
        settings = load_ai_settings(environment={}, secrets_path=Path("/missing/secrets.env"))

        self.assertEqual(settings.max_tokens, 1024)


class AICompactionTests(unittest.TestCase):
    def test_large_lists_keep_counts_differences_and_at_most_five_samples(self) -> None:
        records = [
            {"IP": f"10.0.0.{index}", "passed": index != 12}
            for index in range(30)
        ]

        compacted = compact_fields({"资产记录": records})
        summary = compacted["资产记录"]

        self.assertEqual(summary["总数"], 30)
        self.assertEqual(summary["差异数"], 1)
        self.assertLessEqual(len(summary["样例"]), 5)
        self.assertIn(
            {"IP": "10.0.0.12", "passed": False},
            summary["样例"],
        )

    def test_request_body_is_bounded_and_issues_are_limited_to_ten(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            payload = _item_payload(
                Path(temporary),
                fields={
                    "资产记录": [
                        {
                            "IP": f"10.0.{index // 255}.{index % 255}",
                            "详情": "长文本" * 3000,
                            "passed": index % 17 != 0,
                        }
                        for index in range(300)
                    ]
                },
            )
            payload["normalized"]["issues"] = [f"问题 {index}" for index in range(30)]
            payload["rule_result"]["issues"] = [f"问题 {index}" for index in range(30)]

            request = build_request_payload(payload, _settings())
            encoded = serialize_request(request)
            user_content = request["messages"][1]["content"]
            data = json.loads(user_content.split("\n", 1)[1])

            self.assertLessEqual(len(encoded), 32 * 1024)
            self.assertLessEqual(len(data["问题与证据"]["条目"]), 10)
            self.assertNotIn("issues", data["辅助判定信息"])
            self.assertEqual(data["判定约束"]["最终状态"], "通过")
            self.assertEqual(request["thinking"], {"type": "disabled"})
            system_content = request["messages"][0]["content"]
            self.assertIn("像你亲自完成本项分析一样", system_content)
            self.assertNotIn("程序固定结论", system_content)

    def test_glm_53_flash_request_uses_low_thinking_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = build_request_payload(
                _item_payload(Path(temporary)),
                _settings(model="glm-5.3-flash"),
            )

        self.assertEqual(
            request["thinking"], {"type": "enabled", "clear_thinking": False}
        )
        self.assertEqual(request["reasoning_effort"], "low")


class AIStreamingTests(unittest.TestCase):
    def test_streaming_result_is_saved_and_reused_without_second_request(self) -> None:
        sse = (
            'data: {"choices":[{"delta":{"content":"我核对了社会影响力赋值，其值为2，与稽核要求一致。"}}],"usage":{"prompt_tokens":12}}\n\n'
            'data: {"choices":[{"delta":{"content":"因此，本项判断为通过。"}}]}\n\n'
            'data: {"choices":[],"usage":{"completion_tokens":18}}\n\n'
            'data: [DONE]\n\n'
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertLessEqual(len(request.content), 32 * 1024)
            self.assertEqual(request.headers["authorization"], "Bearer unit-test-key")
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=sse.encode("utf-8"),
            )

        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "DJ-02"
            deltas: list[str] = []
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                service = AuditAIService(_settings(), client=client)
                first = service.generate(
                    _item_payload(item_dir), on_delta=deltas.append
                )
                second_deltas: list[str] = []
                second = service.generate(
                    _item_payload(item_dir), on_delta=second_deltas.append
                )

            self.assertEqual(first["status"], "completed")
            self.assertFalse(first["cached"])
            self.assertEqual(first["usage"], {"prompt_tokens": 12, "completion_tokens": 18})
            self.assertEqual("".join(deltas), first["text"])
            self.assertEqual(second["status"], "completed")
            self.assertTrue(second["cached"])
            self.assertEqual(second_deltas, [second["text"]])
            self.assertEqual(len(requests), 1)

            saved_path = item_dir / "AI复述.json"
            saved = json.loads(saved_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["text"], first["text"])
            self.assertTrue(saved["cached"])
            self.assertNotIn("unit-test-key", saved_path.read_text(encoding="utf-8"))

    def test_missing_conclusion_is_completed_without_second_semantic_call(self) -> None:
        incomplete = (
            'data: {"choices":[{"delta":{"content":"我已核对社会影响力赋值，其值为2，与当前稽核要求保持一致。"},"finish_reason":"stop"}],"usage":{"completion_tokens":80}}\n\n'
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=incomplete.encode("utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            deltas: list[str] = []
            item_dir = Path(temporary) / "DJ-02"
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = AuditAIService(_settings(), client=client).generate(
                    _item_payload(item_dir), on_delta=deltas.append
                )

            saved = json.loads((item_dir / "AI复述.json").read_text(encoding="utf-8"))

        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "completed")
        self.assertTrue(result["text"].endswith("因此，本项判断为通过。"))
        self.assertEqual(result["finish_reason"], "stop")
        self.assertEqual(deltas, [result["text"]])
        self.assertEqual(saved["status"], "completed")

    def test_too_short_response_fails_without_second_semantic_call(self) -> None:
        incomplete = (
            'data: {"choices":[{"delta":{"content":"太短"},"finish_reason":"length"}]}\n\n'
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=incomplete.encode("utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "DJ-02"
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = AuditAIService(_settings(), client=client).generate(
                    _item_payload(item_dir)
                )
            saved = json.loads((item_dir / "AI复述.json").read_text(encoding="utf-8"))

        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "incomplete_response")
        self.assertEqual(result["finish_reason"], "length")
        self.assertNotIn("太短", saved["text"])

    def test_reasoning_only_stream_is_not_exposed_as_narration(self) -> None:
        reasoning_only = (
            'data: {"choices":[{"delta":{"reasoning_content":"内部思考过程"},"finish_reason":"length"}]}\n\n'
        )
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(200, content=reasoning_only.encode("utf-8"))

        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "DJ-02"
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = AuditAIService(_settings(), client=client).generate(
                    _item_payload(item_dir)
                )
            saved = json.loads((item_dir / "AI复述.json").read_text(encoding="utf-8"))

        self.assertEqual(len(requests), 1)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error_code"], "empty_response")
        self.assertEqual(result["finish_reason"], "length")
        self.assertNotIn("内部思考过程", json.dumps(saved, ensure_ascii=False))

    def test_http_error_is_desensitized_and_does_not_raise(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                429,
                content=b'{"error":{"message":"secret-upstream-detail"}}',
            )

        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "DJ-02"
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                result = AuditAIService(_settings(), client=client).generate(
                    _item_payload(item_dir)
                )

            serialized = json.dumps(result, ensure_ascii=False)
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_code"], "rate_limited")
            self.assertIn("AI说明生成失败", result["text"])
            self.assertNotIn("secret-upstream-detail", serialized)
            self.assertNotIn("unit-test-key", serialized)

    def test_timeout_service_error_and_interrupted_stream_are_classified(self) -> None:
        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("secret timeout detail", request=request)

        def unavailable_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, content=b"secret service detail")

        def interrupted_handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=(
                    'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
                    'event: error\ndata: {"error":{"type":"overloaded_error","message":"secret stream detail"}}\n\n'
                ).encode("utf-8"),
            )

        cases = (
            ("timeout", timeout_handler, "timeout"),
            ("server", unavailable_handler, "service_unavailable"),
            ("stream", interrupted_handler, "service_unavailable"),
        )
        for name, handler, expected_code in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                item_dir = Path(temporary) / "DJ-02"
                with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                    result = AuditAIService(_settings(), client=client).generate(
                        _item_payload(item_dir)
                    )
                saved = json.loads(
                    (item_dir / "AI复述.json").read_text(encoding="utf-8")
                )

                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["error_code"], expected_code)
                self.assertEqual(saved["error_code"], expected_code)
                serialized = json.dumps(saved, ensure_ascii=False)
                self.assertNotIn("secret", serialized)

    def test_missing_key_skips_network_and_reports_existing_logic(self) -> None:
        transport = Mock()
        with tempfile.TemporaryDirectory() as temporary:
            item_dir = Path(temporary) / "DJ-02"
            service = AuditAIService(_settings(api_key=""), client=transport)
            result = service.generate(_item_payload(item_dir))
            saved = json.loads(
                (item_dir / "AI复述.json").read_text(encoding="utf-8")
            )

        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["error_code"], "not_configured")
        self.assertEqual(result["text"], "AI未配置，已按现有逻辑完成")
        self.assertEqual(saved["text"], result["text"])
        transport.stream.assert_not_called()


class AuditItemCallbackTests(unittest.TestCase):
    def test_successful_not_applicable_item_calls_callback_after_json_files_exist(self) -> None:
        callback_payloads: list[dict[str, object]] = []

        def callback(payload: dict[str, object]) -> dict[str, object]:
            self.assertTrue(Path(payload["fields_file"]).is_file())
            self.assertTrue(Path(payload["result_file"]).is_file())
            callback_payloads.append(payload)
            return {"status": "completed", "text": "AI复述", "cached": False}

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            source = parsed / "document.md"
            source.write_text("content", encoding="utf-8")
            extractor = SimpleNamespace(extractor=lambda _: {"字段": "值"})
            rule = SimpleNamespace(
                rule=lambda _: {
                    "applicable": False,
                    "passed": False,
                    "message": "当前项不适用",
                    "issues": [],
                }
            )
            with (
                patch("platform_app.audit_service.paired_item_ids", return_value=["TEST-01"]),
                patch("platform_app.audit_service.audit_item_name", return_value="测试项"),
                patch("platform_app.audit_service.get_extractor_spec", return_value=extractor),
                patch("platform_app.audit_service.select_input_documents", return_value=(source,)),
                patch("platform_app.audit_service.get_rule_spec", return_value=rule),
            ):
                summary = run_unit_audits(
                    parsed,
                    root / "audits",
                    on_item_ready=callback,
                )

        self.assertEqual(len(callback_payloads), 1)
        self.assertEqual(callback_payloads[0]["current"], 1)
        self.assertEqual(callback_payloads[0]["total"], 1)
        self.assertEqual(callback_payloads[0]["normalized"]["status"], "not_applicable")
        self.assertEqual(summary["items"][0]["status"], "not_applicable")
        self.assertEqual(summary["items"][0]["ai_narration"]["status"], "completed")

    def test_missing_document_still_calls_llm_before_completion(self) -> None:
        callback = Mock(
            return_value={"status": "completed", "text": "缺失材料已由LLM说明"}
        )
        missing = RequiredDocumentNotFoundError(
            "缺少定级报告", requirement="定级报告"
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            with (
                patch("platform_app.audit_service.paired_item_ids", return_value=["TEST-01"]),
                patch("platform_app.audit_service.audit_item_name", return_value="测试项"),
                patch("platform_app.audit_service.get_extractor_spec", return_value=object()),
                patch("platform_app.audit_service.select_input_documents", side_effect=missing),
            ):
                summary = run_unit_audits(
                    parsed,
                    root / "audits",
                    on_item_ready=callback,
                )

        callback.assert_called_once()
        callback_payload = callback.call_args.args[0]
        self.assertEqual(
            callback_payload["normalized"]["reason_code"],
            "missing_document",
        )
        self.assertEqual(summary["items"][0]["reason_code"], "missing_document")
        self.assertEqual(
            summary["items"][0]["ai_narration"]["status"],
            "completed",
        )

    def test_callback_failure_does_not_change_fixed_rule_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            parsed = root / "parsed"
            parsed.mkdir()
            source = parsed / "document.md"
            source.write_text("content", encoding="utf-8")
            extractor = SimpleNamespace(extractor=lambda _: {"字段": "值"})
            rule = SimpleNamespace(
                rule=lambda _: {"passed": True, "message": "固定结论通过", "issues": []}
            )
            with (
                patch("platform_app.audit_service.paired_item_ids", return_value=["TEST-01"]),
                patch("platform_app.audit_service.audit_item_name", return_value="测试项"),
                patch("platform_app.audit_service.get_extractor_spec", return_value=extractor),
                patch("platform_app.audit_service.select_input_documents", return_value=(source,)),
                patch("platform_app.audit_service.get_rule_spec", return_value=rule),
            ):
                summary = run_unit_audits(
                    parsed,
                    root / "audits",
                    on_item_ready=Mock(side_effect=RuntimeError("sensitive detail")),
                )

            item = summary["items"][0]
            saved = json.loads(
                (root / "audits" / "TEST-01" / "AI复述.json").read_text(encoding="utf-8")
            )

        self.assertEqual(item["status"], "passed")
        self.assertEqual(item["reason_code"], "passed")
        self.assertEqual(item["ai_narration"]["status"], "failed")
        self.assertEqual(saved["error_code"], "callback_error")
        self.assertNotIn("sensitive detail", json.dumps(saved, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
