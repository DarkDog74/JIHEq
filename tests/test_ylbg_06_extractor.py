from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.errors import (
    RequiredDocumentNotFoundError,
    RevisionHistoryNotFoundError,
    TargetSectionNotFoundError,
)
from extractors.ylbg_06 import extract_ylbg_06


PLAN = """# 预案管理名录

| 版本 | 修订记录 |
|---|---|
| V1.0 | 2023年5月12日 |
| V2.0 | 2024年3月1日 |
"""


def report(day: str) -> str:
    return f"# 三、演练时间、地点、人员\n\n（一）时间：{day}\n"


def write_case(root: Path, files: list[dict[str, object]], markdown: dict[str, str]) -> Path:
    for name, content in markdown.items():
        (root / name).write_text(content, encoding="utf-8")
    manifest = root / "应急材料分析.json"
    manifest.write_text(json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8")
    return manifest


class YLBG06ExtractorTests(unittest.TestCase):
    def test_selects_latest_revision_and_latest_year_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_case(
                root,
                [
                    {"用途": "应急预案", "解析状态": "success", "源文件": "plan.docx", "标准Markdown": "应急预案.md"},
                    {"用途": "应急演练报告", "解析状态": "success", "源文件": "old.docx", "标准Markdown": "应急演练报告_2023.md"},
                    {"用途": "应急演练报告", "解析状态": "success", "源文件": "new-a.docx", "标准Markdown": "应急演练报告_2024.md"},
                    {"用途": "应急演练报告", "解析状态": "success", "源文件": "new-b.docx", "标准Markdown": "应急演练报告_2024_2.md"},
                ],
                {
                    "应急预案.md": PLAN,
                    "应急演练报告_2023.md": report("2023年12月31日"),
                    "应急演练报告_2024.md": report("2024年3月1日"),
                    "应急演练报告_2024_2.md": report("2024年3月20日"),
                },
            )
            fields = extract_ylbg_06(manifest)
            self.assertEqual(fields["最新修订记录"]["start_date"], "2024-03-01")
            self.assertEqual(fields["最新修订月份"], "2024-03")
            self.assertEqual(fields["最新演练年份"], 2024)
            self.assertEqual(fields["最新年度演练记录"]["start_date"], "2024-03-20")
            self.assertEqual(fields["最新演练月份"], "2024-03")

    def test_missing_report_is_classified_as_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_case(
                root,
                [{"用途": "应急预案", "解析状态": "success", "源文件": "plan.docx", "标准Markdown": "应急预案.md"}],
                {"应急预案.md": PLAN},
            )
            with self.assertRaises(RequiredDocumentNotFoundError):
                extract_ylbg_06(manifest)

    def test_missing_revision_table_has_specific_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_case(
                root,
                [
                    {"用途": "应急预案", "解析状态": "success", "源文件": "plan.docx", "标准Markdown": "应急预案.md"},
                    {"用途": "应急演练报告", "解析状态": "success", "源文件": "report.docx", "标准Markdown": "应急演练报告.md"},
                ],
                {"应急预案.md": "# 预案正文", "应急演练报告.md": report("2024年3月1日")},
            )
            with self.assertRaises(RevisionHistoryNotFoundError):
                extract_ylbg_06(manifest)

    def test_missing_drill_time_has_specific_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = write_case(
                root,
                [
                    {"用途": "应急预案", "解析状态": "success", "源文件": "plan.docx", "标准Markdown": "应急预案.md"},
                    {"用途": "应急演练报告", "解析状态": "success", "源文件": "report.docx", "标准Markdown": "应急演练报告.md"},
                ],
                {"应急预案.md": PLAN, "应急演练报告.md": "# 演练过程\n\n2024年3月1日"},
            )
            with self.assertRaises(TargetSectionNotFoundError):
                extract_ylbg_06(manifest)


if __name__ == "__main__":
    unittest.main()
