from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.topology_support as topology_support
from audit_rules.topology_support import build_topology_prompt

import audit_rules.fx_06 as rule_mod


def _topology_reply(
    nodes: list[dict],
    confidence: float = 0.9,
    clear: bool | None = True,
) -> ModelReply:
    payload: dict = {
        "节点": nodes,
        "识别说明": "",
        "置信度": confidence,
    }
    if clear is not None:
        payload["拓扑图清晰"] = clear
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False),
        model="fake",
        elapsed_ms=5,
    )


FIELDS = {
    "报告图片": [
        {"id": "IMG-001", "文件": "a.png", "alt": "PDF第12页拓扑图证据", "所在章节": "1.4"},
        {"id": "IMG-002", "文件": "b.png", "alt": "PDF第13页拓扑图证据", "所在章节": "1.4"},
    ],
    "图片文件": [
        {"id": "IMG-001", "路径": "/tmp/IMG-001.png"},
        {"id": "IMG-002", "路径": "/tmp/IMG-002.png"},
    ],
    "拓扑图": [
        {"id": "IMG-001", "文件": "a.png", "定位文字": "- 拓扑图"},
        {"id": "IMG-002", "文件": "b.png", "定位文字": "- 拓扑图"},
    ],
    "资产IP清单": ["10.0.0.1", "10.0.0.2"],
    "资产表": [
        {"资产IP": "10.0.0.1", "资产类型": "防火墙", "资产型号": "FW1"},
        {"资产IP": "10.0.0.2", "资产类型": "服务器", "资产型号": "S1"},
    ],
}


class FX06RuleTests(unittest.TestCase):
    def test_all_assets_in_inventory_passes(self) -> None:
        reply = _topology_reply(
            [
                {"名称": "防火墙", "IP": "10.0.0.1", "类型": "防火墙"},
                {"名称": "S1", "IP": "10.0.0.2", "类型": "服务器"},
            ]
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertTrue(result["passed"])
        self.assertEqual(result["拓扑图数量"], 2)

    def test_extra_ip_fails(self) -> None:
        reply = _topology_reply([{"名称": "防火墙", "IP": "10.0.0.99", "类型": "防火墙"}])
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("10.0.0.99", result["issues"][0])

    def test_no_topology_images_fails_without_model(self) -> None:
        fields = {
            "报告图片": [{"id": "IMG-X", "alt": "机房照片", "所在章节": "2.1"}],
            "拓扑图": [],
            "图片文件": [{"id": "IMG-X", "路径": "/tmp/x.png"}],
            "资产IP清单": [],
            "资产表": [],
        }
        with patch.object(
            topology_support,
            "_chat_with_images",
            side_effect=AssertionError("不应调用模型"),
        ):
            result = rule_mod.audit_fx_06(fields)
        self.assertFalse(result["passed"])
        self.assertIn("拓扑图", result["message"])

    def test_model_error_fails_with_records_kept(self) -> None:
        with patch.object(
            topology_support,
            "_chat_with_images",
            side_effect=ModelNotConfiguredError("视觉模型未配置"),
        ):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        records = result["模型调用记录"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["提示词"], build_topology_prompt(2))
        self.assertIn("视觉模型未配置", records[0]["错误"])

    def test_name_mismatch_but_ip_in_inventory_passes(self) -> None:
        # 口径（2026-08-26）：只核对 IP，不做名称匹配——名称对不上不影响结论
        reply = _topology_reply(
            [{"名称": "资产表里根本不存在的设备名", "IP": "10.0.0.1", "类型": "未知"}]
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertTrue(result["passed"])
        self.assertEqual(result["模型识别IP"], ["10.0.0.1"])
        self.assertNotIn("模型识别名称", result)

    def test_unclear_topology_fails_with_fixed_reason(self) -> None:
        # 判定口径（2026-09 更新）：不清晰直接不通过，理由固定，
        # 即使模型仍提取到 IP 也不进入 IP 核对。
        reply = _topology_reply(
            [{"名称": "防火墙", "IP": "10.0.0.1", "类型": "防火墙"}],
            clear=False,
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertEqual(result["message"], "拓扑图不清晰，无法判定")
        self.assertIn("拓扑图不清晰", result["issues"][0])
        self.assertEqual(result["模型判断拓扑图清晰"], False)
        self.assertNotIn("模型识别IP", result)

    def test_missing_clarity_answer_fails(self) -> None:
        # 模型没回答“拓扑图清晰”：无法证明清晰，按不通过处理。
        reply = _topology_reply(
            [{"名称": "防火墙", "IP": "10.0.0.1", "类型": "防火墙"}],
            clear=None,
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertEqual(result["message"], "拓扑图不清晰，无法判定")

    def test_names_without_ip_fails(self) -> None:
        # 图清晰但没提取到任何 IP → 不通过（名称不算数）
        reply = _topology_reply([{"名称": "核心交换", "IP": None, "类型": "交换机"}])
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("提取到任何 IP", result["issues"][0])

    def test_nothing_recognized_fails(self) -> None:
        reply = _topology_reply([])
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)

    def test_unparseable_reply_keeps_raw_output_in_records(self) -> None:
        """模型已应答但回复不可解析：留档须含原始输出与耗时（铁律 3）。"""
        reply = ModelReply(
            text="抱歉，图片模糊，无法识别拓扑内容。",
            model="fake",
            elapsed_ms=9,
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_fx_06(FIELDS)
        self.assertFalse(result["passed"])
        records = result["模型调用记录"]
        self.assertEqual(records[0]["原始输出"], reply.text)
        self.assertEqual(records[0]["耗时ms"], 9)
        self.assertIn("ModelResponseFormatError", records[0]["错误"])


if __name__ == "__main__":
    unittest.main()
