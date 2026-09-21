from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import platform_app.config as config_mod
from audit_rules.model_support import resolve_staged_image
from audit_rules.topology_support import build_topology_prompt

import audit_rules.topology_support as topology_support

import audit_rules.dj_08 as rule_mod


def _topology_reply(nodes: list[dict], confidence: float = 0.9) -> ModelReply:
    return ModelReply(
        text=json.dumps(
            {"节点": nodes, "识别说明": "", "置信度": confidence}, ensure_ascii=False
        ),
        model="fake",
        elapsed_ms=5,
    )


FIELDS = {
    "拓扑图": {"id": "IMG-002", "文件": "附件/定级报告/IMG-002.png"},
    "图片文件": [{"id": "IMG-002", "路径": "/tmp/IMG-002.png"}],
    "资产IP清单": ["10.0.0.1", "10.0.0.2"],
}


class DJ08RuleTests(unittest.TestCase):
    def test_subset_ips_pass(self) -> None:
        reply = _topology_reply(
            [
                {"名称": "核心交换", "IP": "10.0.0.1", "类型": "交换机"},
                {"名称": "防火墙", "IP": "10.0.0.2", "类型": "防火墙"},
            ]
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_dj_08(FIELDS)
        self.assertTrue(result["passed"])
        self.assertEqual(result["模型识别IP"], ["10.0.0.1", "10.0.0.2"])

    def test_extra_ip_fails(self) -> None:
        reply = _topology_reply([{"名称": "X", "IP": "172.16.0.9", "类型": "未知"}])
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_dj_08(FIELDS)
        self.assertFalse(result["passed"])
        self.assertIn("172.16.0.9", result["issues"][0])

    def test_no_ip_recognized_fails_directly(self) -> None:
        reply = _topology_reply([{"名称": "云平台", "IP": None, "类型": "未知"}])
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_dj_08(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("未能识别到任何 IP", result["message"])

    def test_model_error_fails_with_records_kept(self) -> None:
        with patch.object(
            topology_support,
            "_chat_with_images",
            side_effect=ModelNotConfiguredError("视觉模型未配置"),
        ):
            result = rule_mod.audit_dj_08(FIELDS)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        records = result["模型调用记录"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["提示词"], build_topology_prompt(1))
        self.assertIn("视觉模型未配置", records[0]["错误"])

    def test_unparseable_reply_keeps_raw_output_in_records(self) -> None:
        """模型已应答但回复不可解析：留档须含原始输出与耗时（铁律 3）。"""
        reply = ModelReply(
            text="抱歉，这张拓扑图过于模糊，无法识别其中的节点。",
            model="fake",
            elapsed_ms=7,
        )
        with patch.object(topology_support, "_chat_with_images", return_value=reply):
            result = rule_mod.audit_dj_08(FIELDS)
        self.assertFalse(result["passed"])
        records = result["模型调用记录"]
        self.assertEqual(records[0]["提示词"], build_topology_prompt(1))
        self.assertEqual(records[0]["原始输出"], reply.text)
        self.assertEqual(records[0]["耗时ms"], 7)
        self.assertIn("ModelResponseFormatError", records[0]["错误"])

    def test_staged_image_with_glob_chars_resolves(self) -> None:
        """文件名含 [ ] 的 staged 图片应按相对路径精确回溯，不被当作通配符。"""
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            image = output_root / "系统A" / "audits" / "定级报告" / "图[1]*.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"png")
            decoy = output_root / "系统A" / "audits" / "定级报告" / "图1*.png"
            decoy.write_bytes(b"png")
            entry = {
                "路径": "/staging/已失效/图[1]*.png",
                "相对路径": "audits/定级报告/图[1]*.png",
            }
            with patch.object(config_mod, "OUTPUT_ROOT", output_root):
                resolved = resolve_staged_image(entry)
            self.assertEqual(resolved, image)

    def test_staged_image_expired_fails(self) -> None:
        """相对路径也回溯不到时判不通过，且错误消息指明路径已失效。"""
        with tempfile.TemporaryDirectory() as tmp:
            fields = {
                **FIELDS,
                "图片文件": [
                    {
                        "路径": "/staging/已失效/IMG-002.png",
                        "相对路径": "audits/定级报告/IMG-002.png",
                    }
                ],
            }
            with patch.object(config_mod, "OUTPUT_ROOT", Path(tmp)):
                result = rule_mod.audit_dj_08(fields)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("staged 图片路径已失效", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
