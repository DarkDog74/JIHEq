from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parser.emergency_document_adapter import EmergencyMarkdownResult
from parser.emergency_material_standardizer import (
    classify_emergency_source,
    standardize_emergency_materials,
)


def parsed(markdown: str) -> EmergencyMarkdownResult:
    return EmergencyMarkdownResult(
        document_kind="test",
        source_format="docx",
        conversion_method="test",
        markdown=markdown,
        asset_count=0,
        asset_directory=None,
        images=[],
        warnings=[],
    )


class EmergencyMaterialStandardizerTests(unittest.TestCase):
    def test_classifier_only_accepts_controlled_word_material_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cases = {
                "XX应急预案.docx": "应急预案",
                "XX应急演练方案.docx": "演练方案",
                "XX应急演练总结报告.doc": "应急演练报告",
                "XX应急预案总结.docx": "应急演练报告",
                "LSTP应急方案.docx": None,
                "XX应急预案.wps": None,
                "XX应急响应报告.docx": None,
                "系统资产表.docx": None,
            }
            for name, expected in cases.items():
                path = root / name
                path.write_bytes(b"x")
                self.assertEqual(classify_emergency_source(path), expected, name)

    def test_single_report_uses_stable_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            plan = source / "XX网络安全应急预案.docx"
            report = source / "XX应急演练总结报告.docx"
            scheme = source / "XX应急演练方案.docx"
            plan.write_bytes(b"plan")
            report.write_bytes(b"report")
            scheme.write_bytes(b"scheme")
            report_md = "# 演练时间、地点、人员\n\n（一）时间：2024年3月1日"

            with (
                patch(
                    "parser.emergency_material_standardizer.parse_emergency_plan",
                    return_value=parsed("# 预案"),
                ),
                patch(
                    "parser.emergency_material_standardizer.parse_emergency_drill",
                    return_value=parsed(report_md),
                ),
            ):
                result = standardize_emergency_materials(source, output)

            self.assertEqual(result.success_count, 3)
            self.assertTrue((output / "应急预案.md").is_file())
            self.assertTrue((output / "演练方案.md").is_file())
            self.assertTrue((output / "应急演练报告.md").is_file())
            manifest = json.loads((output / "应急材料分析.json").read_text())
            report_record = next(item for item in manifest["files"] if item["用途"] == "应急演练报告")
            self.assertEqual(report_record["演练年份"], 2024)
            self.assertEqual(report_record["标准Markdown"], "应急演练报告.md")
            scheme_record = next(item for item in manifest["files"] if item["用途"] == "演练方案")
            self.assertEqual(scheme_record["标准Markdown"], "演练方案.md")

    def test_multiple_reports_are_ranked_then_named_latest_previous_and_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            reports = [
                source / "A应急演练总结报告.docx",
                source / "B应急演练总结报告.docx",
                source / "C应急演练总结报告.docx",
            ]
            for report in reports:
                report.write_bytes(b"report")

            def drill(source_path: Path, *_: object, **__: object) -> EmergencyMarkdownResult:
                day = "2024年3月1日" if source_path.name.startswith(("A", "B")) else "2025年5月21日"
                return parsed(f"# 时间、地点、人员\n\n（一）时间：{day}")

            with patch(
                "parser.emergency_material_standardizer.parse_emergency_drill",
                side_effect=drill,
            ):
                standardize_emergency_materials(source, output)

            self.assertEqual(
                sorted(path.name for path in output.glob("应急演练报告*.md")),
                ["应急演练报告.md"],
            )
            self.assertTrue((output / "上一份应急演练报告.md").is_file())
            self.assertTrue((output / "历史应急演练报告_2024-03-01.md").is_file())
            manifest = json.loads((output / "应急材料分析.json").read_text())
            ordered = sorted(
                (item for item in manifest["files"] if item["用途"] == "应急演练报告"),
                key=lambda item: item["排序序号"],
            )
            self.assertEqual([item["标准角色"] for item in ordered], ["最新", "上一份", "历史"])
            self.assertEqual(ordered[0]["标准Markdown"], "应急演练报告.md")
            self.assertEqual(ordered[1]["标准Markdown"], "上一份应急演练报告.md")

    def test_multiple_plans_use_latest_revision_date_for_ranking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            old = source / "A应急预案.docx"
            new = source / "B应急预案.docx"
            old.write_bytes(b"old")
            new.write_bytes(b"new")

            def plan_parser(source_path: Path, *_: object, **__: object) -> EmergencyMarkdownResult:
                day = "2023年5月12日" if source_path.name.startswith("A") else "2024年3月1日"
                return parsed(
                    f"# 预案管理名录\n\n| 版本 | 修订记录 |\n|---|---|\n| V1 | {day} |"
                )

            with patch(
                "parser.emergency_material_standardizer.parse_emergency_plan",
                side_effect=plan_parser,
            ):
                standardize_emergency_materials(source, output)

            self.assertIn("2024年3月1日", (output / "应急预案.md").read_text())
            self.assertIn("2023年5月12日", (output / "上一份应急预案.md").read_text())

    def test_overwrite_replaces_only_previous_emergency_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            output.mkdir()
            (output / "资产清单.md").write_text("keep")
            report = source / "A应急演练总结报告.docx"
            report.write_bytes(b"report")
            report_md = "# 时间、地点、人员\n\n时间：2024年3月1日"
            with patch(
                "parser.emergency_material_standardizer.parse_emergency_drill",
                return_value=parsed(report_md),
            ):
                standardize_emergency_materials(source, output)
                (output / "应急演练报告.md").write_text("stale")
                standardize_emergency_materials(source, output, overwrite=True)
            self.assertEqual((output / "资产清单.md").read_text(), "keep")
            self.assertIn("2024年3月1日", (output / "应急演练报告.md").read_text())


if __name__ == "__main__":
    unittest.main()
