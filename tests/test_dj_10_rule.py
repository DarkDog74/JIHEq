from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.dj_10 as rule_mod
from audit_rules.registry import get_rule_spec


FIELDS = {
    "系统概述": "系统为中国移动的客户提供云业务服务，部署高性能路由器与安全设备。",
}


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=3
    )


def _classification(
    *,
    belongs: bool,
    level: str | None = None,
    network_type: str | None = None,
    a_unit: str | None = None,
    b_unit: str | None = None,
    basis: str = "概述中的功能与候选路径一致",
) -> dict:
    return {
        "是否属于": belongs,
        "匹配层级": level,
        "网络类型": network_type,
        "A类单元": a_unit,
        "B类单元": b_unit,
        "判断依据": basis,
    }


class DJ10RuleTests(unittest.TestCase):
    def test_rule_requires_only_the_system_overview(self) -> None:
        spec = get_rule_spec("DJ-10")
        self.assertEqual(spec.required_fields, ("系统概述",))
        self.assertTrue(spec.uses_model)

    def test_top_level_network_type_passes(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="网络类型",
                network_type="互联网云服务平台",
                basis="概述明确说明向客户提供云业务服务",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertTrue(result["passed"])
        self.assertNotIn("applicable", result)
        self.assertEqual(result["网络类型"], "互联网云服务平台")

    def test_a_class_unit_path_passes(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="A类单元",
                network_type="互联网云服务平台",
                a_unit="互联网公有云服务平台",
                basis="对外提供公有云服务",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertTrue(result["passed"])
        self.assertEqual(result["匹配层级"], "A类单元")
        self.assertEqual(result["网络类型"], "互联网云服务平台")
        self.assertEqual(result["A类单元"], "互联网公有云服务平台")

    def test_b_class_unit_path_passes(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="B类单元",
                network_type="移动通信网",
                a_unit="移动通信网分组域核心网",
                b_unit="5G核心网",
                basis="概述描述5G核心网功能",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertTrue(result["passed"])
        self.assertEqual(result["匹配层级"], "B类单元")
        self.assertEqual(result["网络类型"], "移动通信网")
        self.assertEqual(result["A类单元"], "移动通信网分组域核心网")
        self.assertEqual(result["B类单元"], "5G核心网")

    def test_model_rejects_membership_fails(self) -> None:
        reply = _reply(
            _classification(
                belongs=False,
                basis="概述信息不足，无法对应任一标准路径",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("applicable", result)
        self.assertIn("不属于", result["message"])

    def test_false_conclusion_is_not_overridden_by_a_valid_path(self) -> None:
        reply = _reply(
            _classification(
                belongs=False,
                level="A类单元",
                network_type="互联网云服务平台",
                a_unit="互联网公有云服务平台",
                basis="概述信息不足",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])

    def test_unknown_standard_field_fails_validation(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="A类单元",
                network_type="互联网云服务平台",
                a_unit="不存在的云服务单元",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("《系统类型划分》", result["issues"][0])

    def test_near_but_nonstandard_field_name_fails(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="A类单元",
                network_type="互联网云服务平台",
                a_unit="互联网省级公有云服务平台",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])

    def test_mismatched_parent_child_path_fails(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="B类单元",
                network_type="互联网云服务平台",
                a_unit="互联网公有云服务平台",
                b_unit="5G核心网",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("路径", result["issues"][0])

    def test_invalid_matching_level_fails(self) -> None:
        reply = _reply(
            _classification(
                belongs=True,
                level="C类单元",
                network_type="移动通信网",
            )
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("层级路径", result["issues"][0])

    def test_membership_must_be_a_json_boolean(self) -> None:
        payload = _classification(belongs=False)
        payload["是否属于"] = "false"
        with patch.object(rule_mod, "_chat", return_value=_reply(payload)):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("布尔值", result["issues"][0])

    def test_missing_membership_conclusion_fails(self) -> None:
        payload = _classification(
            belongs=True,
            level="网络类型",
            network_type="互联网云服务平台",
        )
        del payload["是否属于"]
        with patch.object(rule_mod, "_chat", return_value=_reply(payload)):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("是否属于", result["issues"][0])

    def test_model_error_fails_with_records_kept(self) -> None:
        with patch.object(
            rule_mod, "_chat", side_effect=ModelNotConfiguredError("未配置")
        ):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("applicable", result)
        self.assertIn("未配置", result["issues"][0])
        self.assertIn("提示词", result["模型调用记录"][0])

    def test_malformed_model_output_fails(self) -> None:
        reply = ModelReply(text="无法判断", model="fake", elapsed_ms=3)
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_10(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("模型", result["issues"][0])

    def test_prompt_contains_full_hierarchy_and_allows_no_match(self) -> None:
        captured: dict[str, str] = {}

        def fake_chat(prompt, **kwargs):
            captured["prompt"] = prompt
            return _reply(
                _classification(
                    belongs=True,
                    level="A类单元",
                    network_type="互联网云服务平台",
                    a_unit="互联网公有云服务平台",
                )
            )

        with patch.object(rule_mod, "_chat", side_effect=fake_chat):
            rule_mod.audit_dj_10(FIELDS)

        prompt = captured["prompt"]
        self.assertIn(FIELDS["系统概述"], prompt)
        self.assertIn("系统类型划分", prompt)
        for schema_field in (
            "是否属于",
            "匹配层级",
            "网络类型",
            "A类单元",
            "B类单元",
            "判断依据",
        ):
            self.assertIn(schema_field, prompt)
        for standard_field in (
            "互联网云服务平台",
            "互联网公有云服务平台",
            "移动通信网",
            "移动通信网分组域核心网",
            "5G核心网",
        ):
            self.assertIn(standard_field, prompt)
        self.assertIn("信息不足", prompt)
        self.assertIn("false", prompt)
        self.assertNotIn("备案单元", prompt)
        self.assertNotIn("融合边缘云平台", prompt)


if __name__ == "__main__":
    unittest.main()
