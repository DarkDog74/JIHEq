from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.dj_01 as rule_mod
from audit_rules.registry import get_rule_spec


FIELDS = {
    "赋值理由": [
        {"要素": "社会影响力", "理由原文": "服务于地市及以下…", "报告赋值": 2},
        {"要素": "规模和服务范围", "理由原文": "付费用户数100万以下…", "报告赋值": 1},
        {"要素": "提供服务的重要性", "理由原文": "服务于地市及以下…", "报告赋值": 2},
    ],
    "评测表网络类型": "互联网云服务平台",
}

LOCATED_FIELDS = {
    **FIELDS,
    "细化指标章节": {
        "匹配状态": "评测表标题命中",
        "匹配依据": "评测表标题“通信网络安全防护符合性评测表－互联网云服务平台”横线后取词[互联网云服务平台]精确命中 → 互联网云服务平台",
        "专用章节号": "6.17",
        "专用章节标题": "互联网云服务平台",
        "专用章节原文": (
            "## 6.17 互联网云服务平台\n\n表47 社会影响力赋值表\n"
            "（测试用节选：服务于地市及以下各级政府机关 → 2）"
        ),
        "通用章节原文": (
            "## 6.30 其他业务系统\n\n表90 网络/系统单元的社会影响力赋值表\n"
            "（测试用节选：不损害国家安全、社会秩序 → 1）"
        ),
    },
}


def _conclusions(values: dict[str, int], confidence: float = 0.9) -> str:
    payload = {
        "要素结论": [
            {"要素": element, "标准赋值": value, "依据": "表47", "置信度": confidence}
            for element, value in values.items()
        ]
    }
    return json.dumps(payload, ensure_ascii=False)


class DJ01RuleTests(unittest.TestCase):
    def test_rule_is_marked_as_model_backed(self) -> None:
        self.assertTrue(get_rule_spec("DJ-01").uses_model)

    def test_all_elements_match_standard_passes(self) -> None:
        reply = ModelReply(
            text=_conclusions(
                {"社会影响力": 2, "规模和服务范围": 1, "提供服务的重要性": 2}
            ),
            model="fake",
            elapsed_ms=3,
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_01(FIELDS)
        self.assertTrue(result["passed"])
        self.assertIn("符合", result["message"])
        self.assertIn("模型调用记录", result)
        self.assertEqual(result["_precomputed_narration"]["status"], "completed")

    def test_mismatch_fails_with_detail(self) -> None:
        reply = ModelReply(
            text=_conclusions(
                {"社会影响力": 3, "规模和服务范围": 1, "提供服务的重要性": 2}
            ),
            model="fake",
            elapsed_ms=3,
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_01(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("社会影响力：报告赋值 2，按标准应为 3", result["issues"][0])

    def test_model_error_fails(self) -> None:
        # 彻底二元：模型故障也判不通过，留痕保留
        with patch.object(
            rule_mod, "_chat", side_effect=ModelNotConfiguredError("未配置")
        ):
            result = rule_mod.audit_dj_01(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("未配置", result["issues"][0])
        self.assertIn("提示词", result["模型调用记录"][0])

    def test_low_confidence_judges_by_conclusion(self) -> None:
        # 彻底二元：判定跟随模型给出的具体结论，低置信只在结果中附注
        reply = ModelReply(
            text=_conclusions(
                {"社会影响力": 2, "规模和服务范围": 1, "提供服务的重要性": 2},
                confidence=0.3,
            ),
            model="fake",
            elapsed_ms=3,
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_01(FIELDS)
        self.assertTrue(result["passed"])

    def test_null_standard_value_fails(self) -> None:
        # null 兜底仍判不通过：仅当理由已提供的指标按标准也无法定档时
        # （提示词已要求：未提及的可选指标不作为判 null 的原因）
        payload = {
            "要素结论": [
                {"要素": "社会影响力", "标准赋值": 2, "依据": "表47", "置信度": 0.95},
                {"要素": "规模和服务范围", "标准赋值": None,
                 "依据": "标准按用户数取值，报告理由用机架数", "置信度": 0.1},
                {"要素": "提供服务的重要性", "标准赋值": 2, "依据": "表50", "置信度": 0.95},
            ]
        }
        reply = ModelReply(
            text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=3
        )
        with patch.object(rule_mod, "_chat", return_value=reply):
            result = rule_mod.audit_dj_01(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("无法按细化指标判定", result["issues"][0])

    def test_missing_element_in_fields_fails_deterministically(self) -> None:
        broken = {"赋值理由": FIELDS["赋值理由"][:2]}
        result = rule_mod.audit_dj_01(broken)
        self.assertFalse(result["passed"])
        self.assertNotIn("模型调用记录", result)

    def test_prompt_uses_located_sections_when_available(self) -> None:
        # 提取器已定位章节时：提示词只含专用章节+通用赋值表，不再发送标准全文
        prompts: list[str] = []

        def fake_chat(prompt: str, max_tokens: int = 4096):
            prompts.append(prompt)
            return ModelReply(
                text=_conclusions(
                    {"社会影响力": 2, "规模和服务范围": 1, "提供服务的重要性": 2}
                ),
                model="fake",
                elapsed_ms=3,
            )

        with patch.object(rule_mod, "_chat", side_effect=fake_chat):
            result = rule_mod.audit_dj_01(LOCATED_FIELDS)

        self.assertTrue(result["passed"])
        prompt = prompts[0]
        self.assertIn("专用标准章节：6.17 互联网云服务平台", prompt)
        self.assertIn("类型定位依据", prompt)
        self.assertIn("通用赋值方法：其他业务系统", prompt)
        self.assertIn("测试用节选", prompt)
        # 全文才有的章节不得进入提示词
        self.assertNotIn("固定通信网", prompt)
        self.assertNotIn("骨干直联点", prompt)
        self.assertEqual(result["标准章节"]["专用章节"], "6.17 互联网云服务平台")
        self.assertEqual(result["标准章节"]["匹配状态"], "评测表标题命中")

    def test_prompt_uses_generic_method_only_when_no_specific_section(self) -> None:
        unmatched = {
            **FIELDS,
            "评测表网络类型": "魔法平台",
            "细化指标章节": {
                "匹配状态": "未匹配专用章节",
                "匹配依据": "评测表标题的网络类型“魔法平台”未在《系统类型划分》中唯一定位",
                "专用章节号": None,
                "专用章节标题": None,
                "专用章节原文": None,
                "通用章节原文": "## 6.30 其他业务系统\n（测试用节选：表90）",
            },
        }
        prompts: list[str] = []

        def fake_chat(prompt: str, max_tokens: int = 4096):
            prompts.append(prompt)
            return ModelReply(
                text=_conclusions(
                    {"社会影响力": 2, "规模和服务范围": 1, "提供服务的重要性": 2}
                ),
                model="fake",
                elapsed_ms=3,
            )

        with patch.object(rule_mod, "_chat", side_effect=fake_chat):
            result = rule_mod.audit_dj_01(unmatched)

        self.assertTrue(result["passed"])
        prompt = prompts[0]
        self.assertIn("未定位到专用章节", prompt)
        self.assertIn("通用赋值方法：其他业务系统", prompt)
        self.assertNotIn("专用标准章节", prompt)
        self.assertIsNone(result["标准章节"]["专用章节"])

    def test_prompt_falls_back_to_full_standard_without_location_field(self) -> None:
        # 旧字段 JSON（无细化指标章节键）回退标准全文，保持历史行为
        prompts: list[str] = []

        def fake_chat(prompt: str, max_tokens: int = 4096):
            prompts.append(prompt)
            return ModelReply(
                text=_conclusions(
                    {"社会影响力": 2, "规模和服务范围": 1, "提供服务的重要性": 2}
                ),
                model="fake",
                elapsed_ms=3,
            )

        with patch.object(rule_mod, "_chat", side_effect=fake_chat):
            result = rule_mod.audit_dj_01(FIELDS)

        self.assertTrue(result["passed"])
        prompt = prompts[0]
        self.assertIn("## 6.2 固定通信网", prompt)
        self.assertNotIn("类型定位依据", prompt)
        self.assertNotIn("标准章节", result)


if __name__ == "__main__":
    unittest.main()
