from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.stb_00 as rule_mod


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=4
    )


# 真实样本正文：只描述新增扩建，没有“原有…持续运行”类表述
NO_STOCK_BODY = (
    "我公司融合边缘云平台，年度新增（扩建，改建）新增1773台硬件设备，"
    "均已同步配套相关网络安全措施，详细信息见附件，网络安全三同步工作有效落实。"
)

STOCK_BODY = (
    "本年度系统未新增建设。原有网络安全措施仍然持续运行，"
    "既有防火墙、入侵检测等设备持续有效，保障系统安全稳定运行。"
)


def _text_fields(body: str, *, report_list_exists: bool = False) -> dict:
    return {
        "三同步正文": body,
        "图片文件": [],
        "三同步报告存在": True,
        "三同步报告清单存在": report_list_exists,
        "三同步报告清单文件": (
            ["三同步报告新建、改建、扩建设备清单.xlsx"]
            if report_list_exists
            else []
        ),
    }


def _image_fields(tmp: str) -> dict:
    image = Path(tmp) / "audits" / "STB-00" / "三同步.png"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"png")
    return {
        "三同步正文": "",
        "图片文件": [{"路径": str(image), "相对路径": "audits/STB-00/三同步.png"}],
        "三同步报告存在": True,
        "三同步报告清单存在": False,
        "三同步报告清单文件": [],
    }


class STB00RuleTextTests(unittest.TestCase):
    def test_new_construction_with_report_list_passes(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply(
                {"场景": "新改扩", "依据": "本年度新增1773台硬件设备"}
            ),
        ):
            result = rule_mod.audit_stb_00(
                _text_fields(NO_STOCK_BODY, report_list_exists=True)
            )
        self.assertTrue(result["passed"])
        self.assertEqual(result["场景"], "新改扩")
        self.assertTrue(result["三同步报告清单存在"])

    def test_new_construction_without_report_list_fails(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply(
                {"场景": "新改扩", "依据": "本年度扩容核心设备"}
            ),
        ):
            result = rule_mod.audit_stb_00(_text_fields(NO_STOCK_BODY))
        self.assertFalse(result["passed"])
        self.assertIn("缺少三同步报告清单", result["message"])

    def test_stock_description_present_passes(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply(
                {"场景": "存量", "存量说明": "有", "依据": "原有网络安全措施仍然持续运行"}
            ),
        ):
            result = rule_mod.audit_stb_00(_text_fields(STOCK_BODY))
        self.assertTrue(result["passed"])
        self.assertIn("原有网络安全措施仍然持续运行", result["message"])

    def test_real_sample_without_stock_description_fails(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply(
                {"场景": "存量", "存量说明": "无", "依据": ""}
            ),
        ):
            result = rule_mod.audit_stb_00(_text_fields(NO_STOCK_BODY))
        self.assertFalse(result["passed"])
        self.assertIn("存量情况说明", result["issues"][0])

    def test_stock_scene_requires_valid_stock_description_value(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply({"场景": "存量", "存量说明": "不清楚", "依据": ""}),
        ):
            result = rule_mod.audit_stb_00(_text_fields(STOCK_BODY))
        self.assertFalse(result["passed"])
        self.assertIn("存量说明结论无法识别", result["issues"][0])

    def test_low_confidence_judges_by_conclusion(self) -> None:
        # 彻底二元：判定跟随模型结论，低置信不再拦截
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply({"场景": "存量", "存量说明": "有", "依据": "…"}),
        ):
            result = rule_mod.audit_stb_00(_text_fields(STOCK_BODY))
        self.assertTrue(result["passed"])

    def test_unknown_verdict_goes_to_review(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply({"场景": "不清楚", "依据": ""}),
        ):
            result = rule_mod.audit_stb_00(_text_fields(STOCK_BODY))
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("无法识别", result["message"])

    def test_model_error_fails_with_prompt_record(self) -> None:
        with patch.object(
            rule_mod, "_chat", side_effect=ModelNotConfiguredError("文本模型未配置")
        ):
            result = rule_mod.audit_stb_00(_text_fields(STOCK_BODY))
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        records = result["模型调用记录"]
        self.assertIn("存量", records[0]["提示词"])
        self.assertIn("文本模型未配置", records[0]["错误"])

    def test_empty_body_without_images_fails_without_model(self) -> None:
        result = rule_mod.audit_stb_00({"三同步正文": "", "图片文件": []})
        self.assertFalse(result["passed"])
        self.assertNotIn("模型调用记录", result)

    def test_report_exists_flag_false_fails_without_model(self) -> None:
        fields = _text_fields(STOCK_BODY)
        fields["三同步报告存在"] = False
        with patch.object(rule_mod, "_chat") as text_chat:
            result = rule_mod.audit_stb_00(fields)
        self.assertFalse(result["passed"])
        self.assertIn("报告缺失", result["message"])
        text_chat.assert_not_called()

    def test_prompt_explains_negation_and_explicit_change_priority(self) -> None:
        with patch.object(
            rule_mod,
            "_chat",
            return_value=_reply({"场景": "存量", "存量说明": "有", "依据": "无新增"}),
        ) as text_chat:
            rule_mod.audit_stb_00(_text_fields("本年度无新增，原有措施持续运行。"))
        prompt = text_chat.call_args[0][0]
        self.assertIn("未发生新建、改建、扩建", prompt)
        self.assertIn("明确说明本年度", prompt)
        self.assertIn("不要判断文件是否存在", prompt)


class STB00RuleImageTests(unittest.TestCase):
    def test_image_only_report_uses_vision_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = _image_fields(tmp)
            with patch.object(
                rule_mod,
                "_chat_with_images",
                return_value=_reply(
                    {"场景": "存量", "存量说明": "有", "依据": "图中写明原有措施持续运行"}
                ),
            ) as vision, patch.object(rule_mod, "_chat") as text_chat:
                result = rule_mod.audit_stb_00(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(vision.call_count, 1)
        text_chat.assert_not_called()
        self.assertEqual(result["图片数量"], 1)

    def test_mixed_report_feeds_body_and_images_together(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = _image_fields(tmp)
            fields["三同步正文"] = STOCK_BODY
            with patch.object(
                rule_mod,
                "_chat_with_images",
                return_value=_reply(
                    {"场景": "存量", "存量说明": "有", "依据": "原有网络安全措施仍然持续运行"}
                ),
            ) as vision, patch.object(rule_mod, "_chat") as text_chat:
                result = rule_mod.audit_stb_00(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(vision.call_count, 1)
        text_chat.assert_not_called()
        # 正文与图片在同一次视觉调用里一起喂给模型
        prompt, paths = vision.call_args[0]
        self.assertIn("原有网络安全措施仍然持续运行", prompt)
        self.assertEqual(len(paths), 1)

    def test_vision_model_failure_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fields = _image_fields(tmp)
            with patch.object(
                rule_mod,
                "_chat_with_images",
                side_effect=ModelNotConfiguredError("视觉模型未配置"),
            ):
                result = rule_mod.audit_stb_00(fields)
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("视觉模型未配置", result["模型调用记录"][0]["错误"])


if __name__ == "__main__":
    unittest.main()
