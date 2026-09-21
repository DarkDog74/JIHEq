from __future__ import annotations

import unittest

from extractors.fhx_02 import FHX02ExtractionError, extract_fhx_02_text


class FHX02ExtractorTests(unittest.TestCase):
    def test_extracts_only_question_rows_after_target_header(self) -> None:
        result = extract_fhx_02_text(
            """| 填写说明 | 答案包括是、否、不适用 |  |  |
| --- | --- | --- | --- |
| 检查项目 |  |  |  |
| 序号 | 检查点 | 答案 | 答案说明 |
| 网络安全 |  |  |  |
| 01-01 | 是否启用安全功能？ | 是 | 已启用 |
|  | 是否配置备份？ |  |  |
"""
        )

        self.assertEqual(len(result["答案记录"]), 2)
        self.assertEqual(result["答案记录"][0]["答案"], "是")
        self.assertEqual(result["答案记录"][1]["答案"], "")
        self.assertEqual(result["答案记录"][1]["题目行号"], 2)

    def test_supports_answer_column_at_different_position(self) -> None:
        result = extract_fhx_02_text(
            """| 序号 | 检查内容 | 检查点 | 备注 | 答案 |
| --- | --- | --- | --- | --- |
| 01 | 要求A | 是否符合A？ |  | 不 适 用 |
"""
        )
        self.assertEqual(result["答案记录"][0]["答案"], "不 适 用")

    def test_rejects_markdown_without_target_header(self) -> None:
        with self.assertRaisesRegex(FHX02ExtractionError, "检查点.*答案"):
            extract_fhx_02_text("| 问题 | 答案 |\n| --- | --- |\n| A | 是 |\n")


if __name__ == "__main__":
    unittest.main()
