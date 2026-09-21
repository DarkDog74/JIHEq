from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np

from core.model_client import ModelNotConfiguredError, ModelReply

import audit_rules.stb_04 as rule_mod


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=4
    )


def _image_fields(tmp: str, *, valid: bool = True) -> dict:
    if valid:
        image = Path(tmp) / "audits" / "STB-04" / "三同步.jpg"
        image.parent.mkdir(parents=True)
        image.write_bytes(b"jpg")
        entry = {"路径": str(image), "相对路径": "audits/STB-04/三同步.jpg"}
    else:
        entry = {"路径": "/staging/已失效/三同步.jpg", "相对路径": "audits/STB-04/三同步.jpg"}
    return {"has_images": True, "图片数量": 1, "图片文件": [entry]}


def _text_fields() -> dict:
    return {"has_images": False, "图片数量": 0, "报告形态": "仅Markdown", "图片文件": []}


class STB04RuleTests(unittest.TestCase):
    def test_creates_enlarged_detail_for_red_seal_region(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "report.png"
            image = np.full((800, 600, 3), 255, dtype=np.uint8)
            cv2.circle(image, (450, 620), 80, (0, 0, 210), 5)
            cv2.putText(
                image,
                "STAMP",
                (395, 625),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 0, 210),
                2,
            )
            self.assertTrue(cv2.imwrite(str(source), image))

            details = rule_mod._create_stamp_detail_images([source], root / "details")

            self.assertEqual(len(details), 1)
            detail = cv2.imread(str(details[0]))
            self.assertIsNotNone(detail)
            assert detail is not None
            self.assertGreaterEqual(max(detail.shape[:2]), 900)

    def test_markdown_only_report_fails_without_model(self) -> None:
        # 纯 Markdown 报告无图片 → 必然无章，直接不通过（不调模型）
        with patch.object(
            rule_mod,
            "_chat_with_images",
            side_effect=AssertionError("不应调用模型"),
        ):
            result = rule_mod.audit_stb_04(_text_fields())
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("无图片", result["message"])
        self.assertNotIn("模型调用记录", result)

    def test_seal_recognized_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            detail = Path(tmp) / "stamp-detail.png"
            with (
                patch.object(
                    rule_mod,
                    "_create_stamp_detail_images",
                    return_value=[detail],
                ),
                patch.object(
                    rule_mod,
                    "_chat_with_images",
                    return_value=_reply(
                        {
                            "发现红色印章": True,
                            "红色印文包含网管中心": True,
                            "依据": "落款处圆形红章含网管中心",
                        }
                    ),
                ) as vision,
            ):
                result = rule_mod.audit_stb_04(_image_fields(tmp))
        self.assertTrue(result["passed"])
        self.assertEqual(vision.call_count, 1)
        self.assertEqual(len(vision.call_args.args[1]), 2)
        self.assertIn("自动截取并放大", vision.call_args.args[0])
        self.assertEqual(result["印章放大图数量"], 1)
        self.assertIn("网管中心", result["message"])

    def test_no_seal_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                rule_mod,
                "_chat_with_images",
                return_value=_reply(
                    {
                        "发现红色印章": False,
                        "红色印文包含网管中心": False,
                        "依据": "全文未见任何印章",
                    }
                ),
            ):
                result = rule_mod.audit_stb_04(_image_fields(tmp))
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("未见", result["issues"][0])

    def test_any_red_seal_does_not_pass_without_management_center_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                rule_mod,
                "_chat_with_images",
                return_value=_reply(
                    {
                        "发现红色印章": True,
                        "红色印文包含网管中心": False,
                        "红色印文": "中国移动通信集团浙江有限公司",
                        "依据": "发现红章，但未辨认出网管中心",
                    }
                ),
            ):
                result = rule_mod.audit_stb_04(_image_fields(tmp))

        self.assertFalse(result["passed"])
        self.assertIs(result["发现红色印章"], True)
        self.assertEqual(result["红色印文"], "中国移动通信集团浙江有限公司")

    def test_model_error_fails_with_records(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(
                rule_mod,
                "_chat_with_images",
                side_effect=ModelNotConfiguredError("视觉模型未配置"),
            ):
                result = rule_mod.audit_stb_04(_image_fields(tmp))
        self.assertFalse(result["passed"])
        self.assertNotIn("review_required", result)
        self.assertIn("视觉模型未配置", result["模型调用记录"][0]["错误"])

    def test_staged_image_expired_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            import platform_app.config as config_mod

            fields = _image_fields(tmp, valid=False)
            with patch.object(config_mod, "OUTPUT_ROOT", Path(tmp)):
                result = rule_mod.audit_stb_04(fields)
        self.assertFalse(result["passed"])
        self.assertIn("staged 图片路径已失效", result["issues"][0])


if __name__ == "__main__":
    unittest.main()
