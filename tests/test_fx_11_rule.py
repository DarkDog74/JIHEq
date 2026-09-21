from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.fx_11 as rule_mod


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=3
    )


FIELDS = {
    "脆弱性识别章节文本": "识别对象包括物理环境、网络结构、系统软件、应用中间件、应用系统、技术管理、组织管理。",
    "已识别对象清单": ["服务器"],
    "附录D脆弱性记录": [
        {
            "资产名称": "服务器",
            "脆弱性名称": "系统漏洞",
            "脆弱性描述": "补丁未及时安装",
        }
    ],
    "附录D定位": "找到",
}

ALL_COVERED = {
    "覆盖情况": [
        {"类别": category, "已覆盖": True, "证据": category}
        for category in (
            "物理环境",
            "网络结构",
            "系统软件",
            "应用中间件",
            "应用系统",
            "技术管理",
            "组织管理",
        )
    ],
    "置信度": 0.9,
}


class FX11RuleTests(unittest.TestCase):
    def test_model_checks_chapter_not_appendix(self) -> None:
        with patch.object(rule_mod, "_chat", return_value=_reply(ALL_COVERED)) as chat:
            result = rule_mod.audit_fx_11(FIELDS)
        self.assertTrue(result["passed"])
        self.assertIn("模型调用记录", result)
        prompt = chat.call_args.args[0]
        self.assertNotIn("补丁未及时安装", prompt)
        self.assertIn(FIELDS["脆弱性识别章节文本"], prompt)
        self.assertIn("不得根据系统名称或宽泛词语推断覆盖", prompt)
        self.assertNotIn("5.1", prompt)

    def test_no_appendix_required(self) -> None:
        with patch.object(rule_mod, "_chat", return_value=_reply(ALL_COVERED)):
            result = rule_mod.audit_fx_11({"脆弱性识别章节文本": FIELDS["脆弱性识别章节文本"]})
        self.assertTrue(result["passed"])

    def test_empty_chapter_does_not_call_model(self) -> None:
        with patch.object(rule_mod, "_chat") as chat:
            result = rule_mod.audit_fx_11({"脆弱性识别章节文本": ""})
        self.assertFalse(result["passed"])
        chat.assert_not_called()

    def test_fabricated_evidence_and_string_false_do_not_pass(self) -> None:
        for value, evidence in ((True, "章外的物理环境证据"), ("false", "物理环境")):
            payload = json.loads(json.dumps(ALL_COVERED))
            payload["覆盖情况"][0].update({"已覆盖": value, "证据": evidence})
            with self.subTest(value=value), patch.object(rule_mod, "_chat", return_value=_reply(payload)):
                self.assertFalse(rule_mod.audit_fx_11(FIELDS)["passed"])

    def test_missing_category_fails(self) -> None:
        payload = json.loads(json.dumps(ALL_COVERED))
        payload["覆盖情况"][0]["已覆盖"] = False
        with patch.object(rule_mod, "_chat", return_value=_reply(payload)):
            result = rule_mod.audit_fx_11(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("物理环境", result["issues"][0])

    def test_model_error_fails(self) -> None:
        # 彻底二元：模型故障判不通过。
        with patch.object(
            rule_mod, "_chat", side_effect=ModelNotConfiguredError("未配置")
        ):
            result = rule_mod.audit_fx_11(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("模型调用失败", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
