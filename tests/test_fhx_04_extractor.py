from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fhx_04 import FHX04ExtractionError, extract_fhx_04, extract_fhx_04_text


FHX04_MARKDOWN = """| 填写说明 | 内容 |  |  |
| --- | --- | --- | --- |
| 检查项目 |  |  |  |
| 序号 | 检查点 | 答案 | 答案说明 |
| 章节标题 |  |  |  |
| 01 | 是否启用认证 | 是 | 已启用4A认证 |
| 02 | 是否限制访问 | 是 |  |
| 03 | 是否有截图 | 是 | =DISPIMG("ID_001",1) |
| 04 | 是否有材料 | 是 | 见证明材料 |
"""


class FHX04ExtractorTests(unittest.TestCase):
    def test_only_empty_explanations_require_proof(self) -> None:
        result = extract_fhx_04_text(FHX04_MARKDOWN, proof_material_exists=False)

        self.assertEqual(result["评测项总数"], 4)
        self.assertEqual(result["答案说明非空项数"], 3)
        self.assertEqual(
            [item["需证明材料原因"] for item in result["答案说明为空项"]],
            ["答案说明为空"],
        )

    def test_whitespace_is_empty_but_reference_and_placeholder_text_are_not(self) -> None:
        for value, empty in [("　 \t", True), ("见机房文档", False), ("待补充", False), ("/", False)]:
            with self.subTest(value=value):
                result = extract_fhx_04_text(
                    f"| 序号 | 检查点 | 答案 | 答案说明 |\n| 1 | A | 是 | {value} |",
                    proof_material_exists=False,
                )
                self.assertEqual(len(result["答案说明为空项"]), int(empty))

    def test_blank_checkpoint_with_an_answer_is_still_checked(self) -> None:
        result = extract_fhx_04_text(
            "| 序号 | 检查点 | 答案 | 答案说明 |\n| 1 | | 是 | |",
            proof_material_exists=True,
        )
        self.assertEqual(len(result["答案说明为空项"]), 1)

    def test_unrelated_table_and_blank_reserved_row_are_not_checked(self) -> None:
        result = extract_fhx_04_text(
            "| 名称 | 内容 |\n| 空白说明 | |\n\n"
            "| 答案说明 | 答案 | 检查点 | 序号 |\n"
            "| 已说明 | 是 | A | 1 |\n| | | | |\n\n"
            "| 名称 | 内容 |\n| B | |",
            proof_material_exists=False,
        )
        self.assertEqual(result["评测项总数"], 1)
        self.assertEqual(result["答案说明为空项"], [])

    def test_detects_optional_proof_material_in_same_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "符合性评测表.md"
            proof = root / "符合性评测表证明材料.md"
            report.write_text(FHX04_MARKDOWN, encoding="utf-8")
            proof.write_text("# 证明材料", encoding="utf-8")

            result = extract_fhx_04(report)

            self.assertTrue(result["证明材料存在"])
            self.assertEqual(result["证明材料路径"], str(proof))

    def test_numbered_proof_file_exists_without_reading_its_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "符合性评测表.md"
            report.write_text(FHX04_MARKDOWN, encoding="utf-8")
            # Invalid UTF-8 deliberately proves the proof file is not read.
            proof = root / "符合性评测表证明材料（2）.md"
            proof.write_bytes(b"\xff\xfe")
            result = extract_fhx_04(report)
            self.assertTrue(result["证明材料存在"])
            self.assertEqual(result["证明材料路径"], str(proof))

    def test_proof_directory_or_other_system_proof_does_not_count(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "符合性评测表.md"
            report.write_text(FHX04_MARKDOWN, encoding="utf-8")
            (root / "符合性评测表证明材料.md").mkdir()
            other = root / "其他系统"
            other.mkdir()
            (other / "符合性评测表证明材料.md").touch()
            self.assertFalse(extract_fhx_04(report)["证明材料存在"])

    def test_empty_proof_file_counts_as_existing_not_as_validated_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "符合性评测表.md"
            report.write_text(FHX04_MARKDOWN, encoding="utf-8")
            (root / "符合性评测表证明材料.md").touch()
            self.assertTrue(extract_fhx_04(report)["证明材料存在"])

    def test_no_actual_records_is_an_extraction_error_not_a_pass(self) -> None:
        with self.assertRaisesRegex(FHX04ExtractionError, "没有可检查"):
            extract_fhx_04_text(
                "| 序号 | 检查点 | 答案 | 答案说明 |\n| 分类标题 | | | |",
                proof_material_exists=True,
            )

    def test_rejects_markdown_without_target_table(self) -> None:
        with self.assertRaisesRegex(FHX04ExtractionError, "检查点、答案、答案说明"):
            extract_fhx_04_text(
                "| 名称 | 内容 |\n| --- | --- |\n| A | B |\n",
                proof_material_exists=False,
            )


if __name__ == "__main__":
    unittest.main()
