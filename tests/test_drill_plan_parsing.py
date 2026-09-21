from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from parsing_engine import preview_source


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER_ROOT = PROJECT_ROOT / "parser"
PARSER = PARSER_ROOT / "parse_folder.py"
PARSER_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


class DrillPlanParsingTests(unittest.TestCase):
    def test_preview_recognizes_supported_drill_plan_workbook(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory) / "部门A" / "测试系统"
            unit.mkdir(parents=True)
            (unit / "2026年网络信息安全应急演练计划-7月.xlsx").write_bytes(b"xlsx")
            (unit / "2026年网络信息安全应急演练方案.xlsx").write_bytes(b"xlsx")

            preview = preview_source(unit)

            self.assertEqual(preview["category_counts"]["演练计划"], 1)
            self.assertNotIn("演练计划", preview["missing_categories"])
            self.assertEqual(
                [item["name"] for item in preview["documents"]],
                ["2026年网络信息安全应急演练计划-7月.xlsx"],
            )

    def test_parser_uses_anydoc_and_writes_standard_drill_plan_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            fake_modules = root / "fake_modules"
            source.mkdir()
            fake_modules.mkdir()
            (source / "2026年网络信息安全应急演练计划-7月.xlsx").write_bytes(b"xlsx")
            (fake_modules / "anydoc.py").write_text(
                "def format_from_bytes(data):\n"
                "    return 'xlsx'\n"
                "def format_from_path(path):\n"
                "    return 'xlsx'\n"
                "def to_markdown_bytes(data, detected):\n"
                "    assert detected == 'xlsx'\n"
                "    return '# 基础安全演练计划\\n\\n| 系统 | 勒索病毒 |\\n|---|---|\\n| 测试系统 | √ |\\n'\n",
                encoding="utf-8",
            )
            environment = os.environ.copy()
            environment["PYTHONPATH"] = os.pathsep.join(
                [str(fake_modules), environment.get("PYTHONPATH", "")]
            )

            completed = subprocess.run(
                [
                    str(PARSER_PYTHON if PARSER_PYTHON.is_file() else Path(sys.executable)),
                    str(PARSER),
                    str(source),
                    "--output",
                    str(output),
                    "--only",
                    "2.10",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )

            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertEqual(
                (output / "演练计划.md").read_text(encoding="utf-8"),
                "# 基础安全演练计划\n\n| 系统 | 勒索病毒 |\n|---|---|\n| 测试系统 | √ |\n",
            )
            analysis = json.loads((output / "文件分析.json").read_text(encoding="utf-8"))
            self.assertEqual(analysis["files"][0]["category_code"], "2.10")
            self.assertEqual(analysis["files"][0]["status"], "success")
            self.assertEqual(analysis["files"][0]["output"], "演练计划.md")

    def test_batch_summary_includes_drill_plan_counts_and_csv_columns(self) -> None:
        sys.path.insert(0, str(PARSER_ROOT))
        try:
            from batch_parse_folders import unit_summary, write_batch_manifest
        finally:
            sys.path.pop(0)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output_root = root / "batch"
            unit_output = output_root / "部门A" / "测试系统"
            unit_output.mkdir(parents=True)
            (unit_output / "文件分析.json").write_text(
                json.dumps(
                    {
                        "files": [
                            {
                                "source": "2026年演练计划.xlsx",
                                "category_code": "2.10",
                                "category_name": "演练计划",
                                "status": "success",
                            }
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            summary = unit_summary(unit_output)
            self.assertEqual(summary["drill_plans_found"], 1)
            self.assertEqual(summary["drill_plans_success"], 1)
            self.assertEqual(summary["drill_plans_unsupported"], 0)

            row = {
                "department": "部门A",
                "unit_folder": "测试系统",
                "input": str(root / "source"),
                "output": str(unit_output),
                "status": "success",
                "exit_code": 0,
                **summary,
            }
            write_batch_manifest(output_root, root / "source", [row], None)
            with (output_root / "批量汇总.csv").open(encoding="utf-8-sig", newline="") as stream:
                csv_row = next(csv.DictReader(stream))
            self.assertEqual(csv_row["drill_plans_found"], "1")
            self.assertEqual(csv_row["drill_plans_success"], "1")
            self.assertEqual(csv_row["drill_plans_unsupported"], "0")


if __name__ == "__main__":
    unittest.main()
