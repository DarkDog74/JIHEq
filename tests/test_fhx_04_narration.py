from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_rules.fhx_04 import audit_fhx_04
from platform_app.ai_service import AuditAIService, _input_sha256, build_request_payload
from platform_app.config import AISettings


def settings() -> AISettings:
    return AISettings(enabled=True, api_key="unit-test-key")


def payload(root: Path, *, blank: bool = True, proof: bool = True) -> dict:
    fields = {
        "评测项总数": 1,
        "答案说明非空项数": int(not blank),
        "答案说明为空项": [{"序号": "01", "检查点": ""}] if blank else [],
        "证明材料存在": proof,
    }
    result = audit_fhx_04(fields)
    return {
        "item_id": "FHX-04",
        "item_name": "检查缺少答案说明的评测项是否提供证明材料",
        "fields": fields,
        "rule_result": result,
        "normalized": {
            "status": "passed" if result["passed"] else "failed",
            "message": result["message"],
            "issues": result["issues"],
        },
        "item_dir": root,
    }


class FHX04NarrationTests(unittest.TestCase):
    def test_prompt_does_not_send_numbered_records_or_claim_proof_completeness(self) -> None:
        request = build_request_payload(payload(Path("/unused")), settings())
        data = json.loads(request["messages"][1]["content"].split("\n", 1)[1])
        self.assertEqual(set(data), {"稽核编号", "限定说明"})
        self.assertEqual(
            data["限定说明"],
            "符合性评测表的答案说明存在空项，证明材料文件存在。因此，本项判断为通过。",
        )
        self.assertIn("不得评价编号对应", request["messages"][0]["content"])

    def test_all_three_outcomes_still_call_model_then_publish_only_scoped_text(self) -> None:
        for blank, proof in [(False, False), (True, True), (True, False)]:
            with self.subTest(blank=blank, proof=proof), tempfile.TemporaryDirectory() as directory:
                data = payload(Path(directory), blank=blank, proof=proof)
                label = "通过" if data["normalized"]["status"] == "passed" else "不通过"
                expected = f"符合性评测表的{data['normalized']['message']}。因此，本项判断为{label}。"
                deltas = []
                with patch.object(AuditAIService, "_stream", return_value=(expected, {}, "stop")) as stream:
                    service = AuditAIService(settings())
                    result = service.generate(data, on_delta=deltas.append)
                    cached = service.generate(data)
                stream.assert_called_once()
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["text"], expected)
                self.assertEqual(deltas, [expected])
                self.assertTrue(cached["cached"])

    def test_overclaim_is_not_published_or_saved_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = payload(Path(directory))
            bad = "我已逐项核对全部九个编号，所有证明材料均已配套提供且真实有效。因此，本项判断为通过。"
            deltas = []
            with patch.object(AuditAIService, "_stream", return_value=(bad, {}, "stop")) as stream:
                result = AuditAIService(settings()).generate(data, on_delta=deltas.append)
            stream.assert_called_once()
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["error_code"], "out_of_scope_response")
            self.assertEqual(deltas, [])
            saved = json.loads((Path(directory) / "AI复述.json").read_text())
            self.assertEqual(saved["status"], "failed")
            self.assertNotIn("真实有效", saved["text"])

    def test_corrupt_cached_overclaim_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            data = payload(Path(directory))
            expected = "符合性评测表的答案说明存在空项，证明材料文件存在。因此，本项判断为通过。"
            service = AuditAIService(settings())
            with patch.object(service, "_stream", return_value=(expected, {}, "stop")):
                service.generate(data)
            cache = Path(directory) / "AI复述.json"
            saved = json.loads(cache.read_text())
            saved["text"] = "我核对了全部条目，九项证明材料真实有效且全部齐全。因此，本项判断为通过。"
            cache.write_text(json.dumps(saved, ensure_ascii=False))
            with patch.object(service, "_stream", return_value=(expected, {}, "stop")) as stream:
                result = service.generate(data)
            stream.assert_called_once()
            self.assertFalse(result["cached"])
            self.assertEqual(result["text"], expected)

    def test_item_specific_version_invalidates_only_fhx04_cache(self) -> None:
        data = payload(Path("/unused"))
        old = _input_sha256(data, settings())
        other = dict(data, item_id="FHX-01")
        other_old = _input_sha256(other, settings())
        with patch("platform_app.ai_service.FHX04_NARRATION_VERSION", "next-version"):
            self.assertNotEqual(_input_sha256(data, settings()), old)
            self.assertEqual(_input_sha256(other, settings()), other_old)


if __name__ == "__main__":
    unittest.main()
