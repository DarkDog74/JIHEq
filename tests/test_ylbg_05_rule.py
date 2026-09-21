from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.model_client import ModelCallError, ModelReply

from audit_rules import ylbg_05 as rule_mod


def _reply(has_watermark: bool, basis: str = "画面右下角有日期时间叠字") -> ModelReply:
    return ModelReply(
        text=json.dumps(
            {
                "有时间水印": has_watermark,
                "水印文字": "2025-05-21 14:30" if has_watermark else "",
                "水印位置": "右下" if has_watermark else "",
                "依据": basis,
                "分析说明": "已逐图核验",
            },
            ensure_ascii=False,
        ),
        model="fake-vision",
        elapsed_ms=3,
    )


def _fields(root: Path, names: list[str]) -> dict:
    checklist = []
    staged = []
    for number, name in enumerate(names, start=1):
        path = root / name
        path.write_bytes(f"image-{number}".encode())
        checklist.append(
            {
                "图片序号": number,
                "图片ID": f"IMG-{number:03d}",
                "文件": f"附件/应急演练报告/{name}",
                "图片名称": f"现场照片{number}",
                "可核验": True,
                "无法核验原因": None,
            }
        )
        staged.append(
            {
                "图片序号": number,
                "图片ID": f"IMG-{number:03d}",
                "文件": f"附件/应急演练报告/{name}",
                "路径": str(path),
            }
        )
    return {"图片数量": len(checklist), "图片清单": checklist, "图片文件": staged}


class YLBG05RuleTests(unittest.TestCase):
    def test_every_image_is_sent_separately_and_all_watermarks_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = _fields(Path(directory), ["one.jpg", "two.png"])
            with patch.object(
                rule_mod, "_chat_with_images", side_effect=[_reply(True), _reply(True)]
            ) as model:
                result = rule_mod.audit_ylbg_05(fields)

            self.assertTrue(result["passed"])
            self.assertEqual(model.call_count, 2)
            self.assertTrue(all(len(call.args[1]) == 1 for call in model.call_args_list))
            self.assertIn("独立、高对比", model.call_args_list[0].args[0])
            self.assertEqual(result["watermarked_image_count"], 2)
            self.assertEqual(result["missing_watermark_count"], 0)

    def test_missing_watermark_names_exact_image_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = _fields(
                Path(directory), ["first.jpg", "missing-watermark.png", "third.jpg"]
            )
            with patch.object(
                rule_mod,
                "_chat_with_images",
                side_effect=[
                    _reply(True),
                    _reply(False, "画面内未见日期或时间水印"),
                    _reply(True),
                ],
            ) as model:
                result = rule_mod.audit_ylbg_05(fields)

            self.assertFalse(result["passed"])
            self.assertEqual(model.call_count, 3)
            self.assertEqual(result["missing_watermark_count"], 1)
            self.assertIn("IMG-002", result["issues"][0])
            self.assertIn("missing-watermark.png", result["issues"][0])
            self.assertEqual(result["图片判定"][1]["判定"], "无时间水印")

    def test_uncheckable_image_fails_but_other_images_are_still_checked(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = _fields(Path(directory), ["valid.jpg"])
            fields["图片清单"].insert(
                0,
                {
                    "图片序号": 2,
                    "图片ID": "IMG-002",
                    "文件": "附件/应急演练报告/missing.jpg",
                    "图片名称": "缺失附件",
                    "可核验": False,
                    "无法核验原因": "解析出的图片文件不存在",
                },
            )
            fields["图片数量"] = 2

            with patch.object(
                rule_mod, "_chat_with_images", return_value=_reply(True)
            ) as model:
                result = rule_mod.audit_ylbg_05(fields)

            self.assertFalse(result["passed"])
            self.assertEqual(model.call_count, 1)
            self.assertEqual(result["uncheckable_image_count"], 1)
            self.assertTrue(any("IMG-002" in issue for issue in result["issues"]))

    def test_model_error_and_invalid_json_are_both_uncheckable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = _fields(Path(directory), ["one.jpg", "two.png"])
            invalid = ModelReply(text="不是JSON", model="fake-vision", elapsed_ms=2)
            with patch.object(
                rule_mod,
                "_chat_with_images",
                side_effect=[ModelCallError("超时"), invalid],
            ):
                result = rule_mod.audit_ylbg_05(fields)

            self.assertFalse(result["passed"])
            self.assertEqual(result["uncheckable_image_count"], 2)
            self.assertEqual(len(result["模型调用记录"]), 2)

    def test_image_read_error_is_recorded_and_does_not_stop_later_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            fields = _fields(Path(directory), ["broken.jpg", "valid.png"])
            with patch.object(
                rule_mod,
                "_chat_with_images",
                side_effect=[OSError("读取失败"), _reply(True)],
            ) as model:
                result = rule_mod.audit_ylbg_05(fields)

            self.assertFalse(result["passed"])
            self.assertEqual(model.call_count, 2)
            self.assertEqual(result["uncheckable_image_count"], 1)
            self.assertEqual(result["watermarked_image_count"], 1)
            self.assertTrue(any("IMG-001" in issue for issue in result["issues"]))

    def test_no_real_images_fails_without_model_call(self) -> None:
        with patch.object(rule_mod, "_chat_with_images") as model:
            result = rule_mod.audit_ylbg_05({"图片数量": 0, "图片清单": []})

        self.assertFalse(result["passed"])
        self.assertIn("未解析出任何真实图片", result["issues"][0])
        model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
