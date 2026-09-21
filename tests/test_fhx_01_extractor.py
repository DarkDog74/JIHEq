from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.errors import RequiredDocumentNotFoundError
from extractors.fhx_01 import (
    FHX01ExtractionError,
    extract_evaluation_ids,
    extract_fhx_01_documents,
    extract_proof_ids,
)


EVALUATION = """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 04-01-01-01-01 | A | 不适用 |  |
| 04-05-01-01-01 | B | 是 | 符合 |
| 04-05-01-02-01 | C | 否 | 不符合 |
| 04-05-01-03-01 | D | 请选择... |  |
"""


class FHX01ExtractorTests(unittest.TestCase):
    def test_extracts_all_evaluation_ids_regardless_of_answer(self) -> None:
        items = extract_evaluation_ids(EVALUATION)
        self.assertEqual(
            [item["编号"] for item in items],
            [
                "04-01-01-01-01",
                "04-05-01-01-01",
                "04-05-01-02-01",
                "04-05-01-03-01",
            ],
        )
        self.assertEqual(
            [item["需证明材料"] for item in items],
            [True, False, False, True],
        )

    def test_dispimg_marker_counts_as_in_table_proof_for_fhx_01(self) -> None:
        items = extract_evaluation_ids(
            """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 04-01-01-01-01 | A | 是 | 见证明材料 |
| 04-01-01-01-02 | B | 是 | =DISPIMG(\"ID_1\",1) |
| 04-01-01-01-03 | C | 是 | 已启用访问控制 |
"""
        )
        self.assertEqual(
            [item["需证明材料"] for item in items],
            [True, False, False],
        )

    def test_dispimg_marker_does_not_require_separate_proof_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = Path(directory) / "符合性评测表.md"
            evaluation.write_text(
                """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 04-01-01-01-01 | A | 是 | =DISPIMG(\"ID_1\",1) |
| 04-01-01-01-02 | B | 是 | 已启用认证 |
""",
                encoding="utf-8",
            )

            result = extract_fhx_01_documents(evaluation)

        self.assertEqual(result["需证明材料编号"], [])
        self.assertEqual(result["证明材料全部编号"], [])
        self.assertEqual(result["证明材料文件"], [])

    def test_extracts_five_part_ids_only_at_proof_item_start(self) -> None:
        items = extract_proof_ids(
            """## 04－05－01－01－01
04-05-01-02-01证明内容
| **04-05-01-03-01**其他内容 |
正文引用04-05-01-04-01，不是条目开头。
## 04-05-01-01
""",
            "证明.md",
        )
        self.assertEqual(
            [item["编号"] for item in items],
            ["04-05-01-01-01", "04-05-01-02-01", "04-05-01-03-01"],
        )

    def test_uses_known_ids_when_question_starts_with_digits(self) -> None:
        items = extract_proof_ids(
            """10-01-02-02-012级和3级节点时钟是否正常？
10-03-01-02-012048kbit/s接口是否正常？
10-04-02-02-022级节点时钟是否正常？
10-04-02-02-033级节点时钟是否正常？
45-01-01-02-1002级节点是否正常？
99-01-01-01-01额外编号仍需识别
""",
            "证明.md",
            known_ids={
                "10-01-02-02-01",
                "10-03-01-02-01",
                "10-04-02-02-02",
                "10-04-02-02-03",
                "45-01-01-02-100",
            },
        )

        self.assertEqual(
            [item["编号"] for item in items],
            [
                "10-01-02-02-01",
                "10-03-01-02-01",
                "10-04-02-02-02",
                "10-04-02-02-03",
                "45-01-01-02-100",
                "99-01-01-01-01",
            ],
        )

    def test_prefers_longest_known_id_prefix(self) -> None:
        items = extract_proof_ids(
            "10-01-02-02-012级节点是否正常？",
            "证明.md",
            known_ids={"10-01-02-02-01", "10-01-02-02-012"},
        )

        self.assertEqual([item["编号"] for item in items], ["10-01-02-02-012"])

    def test_does_not_treat_six_part_id_as_five_part_id(self) -> None:
        items = extract_proof_ids(
            "10-01-02-02-01-02 这是六段编号",
            "证明.md",
            known_ids={"10-01-02-02-01"},
        )

        self.assertEqual(items, [])

    def test_document_extraction_passes_evaluation_ids_to_proof_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation = root / "符合性评测表.md"
            evaluation.write_text(
                """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 10-01-02-02-01 | A | 是 |  |
""",
                encoding="utf-8",
            )
            (root / "符合性评测表证明材料.md").write_text(
                "10-01-02-02-012级和3级节点时钟是否正常？",
                encoding="utf-8",
            )

            result = extract_fhx_01_documents(evaluation)

        self.assertEqual(
            [item["编号"] for item in result["证明材料全部编号"]],
            ["10-01-02-02-01"],
        )

    def test_combines_all_proof_material_documents(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evaluation = root / "符合性评测表.md"
            evaluation.write_text(EVALUATION, encoding="utf-8")
            (root / "符合性评测表证明材料.md").write_text(
                "## 04-05-01-01-01", encoding="utf-8"
            )
            (root / "符合性评测表证明材料（2）.md").write_text(
                "04-05-01-02-01", encoding="utf-8"
            )

            result = extract_fhx_01_documents(evaluation)

        self.assertEqual(len(result["证明材料文件"]), 2)
        self.assertEqual(
            [item["编号"] for item in result["证明材料全部编号"]],
            ["04-05-01-01-01", "04-05-01-02-01"],
        )
        self.assertEqual(
            [item["编号"] for item in result["需证明材料编号"]],
            ["04-01-01-01-01", "04-05-01-03-01"],
        )

    def test_reports_missing_proof_material_as_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = Path(directory) / "符合性评测表.md"
            evaluation.write_text(EVALUATION, encoding="utf-8")
            with self.assertRaises(RequiredDocumentNotFoundError):
                extract_fhx_01_documents(evaluation)

    def test_does_not_require_proof_document_when_all_explanations_are_substantive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            evaluation = Path(directory) / "符合性评测表.md"
            evaluation.write_text(
                """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 04-01-01-01-01 | A | 是 | 已启用认证 |
| 04-01-01-01-02 | B | 是 | 已限制访问 |
""",
                encoding="utf-8",
            )
            result = extract_fhx_01_documents(evaluation)

        self.assertEqual(result["需证明材料编号"], [])
        self.assertEqual(result["证明材料全部编号"], [])
        self.assertEqual(result["证明材料文件"], [])

    def test_rejects_evaluation_without_five_part_ids(self) -> None:
        with self.assertRaisesRegex(FHX01ExtractionError, "五段编号"):
            extract_evaluation_ids(EVALUATION.replace("04-", "编号-"))


if __name__ == "__main__":
    unittest.main()
