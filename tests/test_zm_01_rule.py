from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from core.model_client import ModelCallError, ModelReply

import audit_rules.zm_01 as rule_mod


def _reply(payload: dict) -> ModelReply:
    return ModelReply(
        text=json.dumps(payload, ensure_ascii=False), model="fake", elapsed_ms=4
    )


MATCH = {"对应": True, "无法判断": False, "依据": "截图为配置界面", "置信度": 0.95}
MISMATCH = {"对应": False, "无法判断": False, "依据": "截图与说明无关", "置信度": 0.95}
UNSURE = {"对应": False, "无法判断": True, "依据": "截图过糊", "置信度": 0.5}


def _fields(items: list[dict]) -> dict:
    return {
        "检查项": items,
        "图片文件": [
            {
                "id": shot_id,
                "说明": f"检查项 {code} 截图",
                "路径": f"/tmp/{code}-{shot_id}.png",
            }
            for code, shot_id in [
                (item["检查项编号"], item["截图"][0]["id"])
                for item in items
                if item.get("截图")
            ]
        ],
    }


def _item(code: str, screenshots: list[str]) -> dict:
    return {
        "检查项编号": code,
        "说明文字": "应具有数据敏感度的界定标准。",
        "截图": [{"id": shot, "文件": f"{shot}.png"} for shot in screenshots],
    }


class ZM01RuleTests(unittest.TestCase):
    def test_all_matched_passes(self) -> None:
        fields = _fields([_item("23-01-01-01-01", ["IMG-1"]), _item("23-01-01-02-01", ["IMG-2"])])
        with patch.object(rule_mod, "_chat_with_images", return_value=_reply(MATCH)):
            result = rule_mod.audit_zm_01(fields)
        self.assertTrue(result["passed"])
        self.assertEqual(result["message"], "2 个检查项截图与说明均对应")

    def test_item_without_screenshot_fails_without_model(self) -> None:
        fields = _fields([_item("23-01-01-01-01", []), _item("23-01-01-02-01", ["IMG-2"])])
        with patch.object(rule_mod, "_chat_with_images", return_value=_reply(MATCH)) as fake:
            result = rule_mod.audit_zm_01(fields)
        self.assertFalse(result["passed"])
        self.assertIn("23-01-01-01-01：未提供截图", result["issues"][0])
        self.assertEqual(fake.call_count, 1)

    def test_unsure_item_goes_to_review(self) -> None:
        fields = _fields([_item("23-01-01-01-01", ["IMG-1"])])
        with patch.object(rule_mod, "_chat_with_images", return_value=_reply(UNSURE)):
            result = rule_mod.audit_zm_01(fields)
        self.assertTrue(result["review_required"])

    def test_model_failure_retries_once_then_review(self) -> None:
        fields = _fields([_item("23-01-01-01-01", ["IMG-1"])])
        with patch.object(
            rule_mod, "_chat_with_images", side_effect=ModelCallError("超时")
        ) as fake:
            result = rule_mod.audit_zm_01(fields)
        self.assertTrue(result["review_required"])
        self.assertEqual(fake.call_count, 2)


if __name__ == "__main__":
    unittest.main()
