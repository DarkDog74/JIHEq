from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from parsing_engine.service import ParseResult, _parser_python, preview_source, run_unit_parse


class ParsingEngineServiceTests(unittest.TestCase):
    def test_preview_recognizes_proof_material_without_conformity_in_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "测试系统"
            source.mkdir()
            (source / "证明材料.docx").write_bytes(b"placeholder")

            preview = preview_source(source)

            self.assertEqual(preview["recognized_count"], 1)
            self.assertEqual(
                (
                    preview["documents"][0]["category_code"],
                    preview["documents"][0]["category_name"],
                ),
                ("2.6", "符合性评测表证明材料"),
            )

    def test_preview_recognizes_emergency_only_source_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "应急材料系统"
            source.mkdir()
            for name in (
                "XX网络安全应急预案.docx",
                "XX应急演练方案.doc",
                "XX应急演练总结报告.docx",
            ):
                (source / name).write_bytes(b"placeholder")

            preview = preview_source(source)

            self.assertEqual(preview["recognized_count"], 3)
            self.assertEqual(
                {
                    (item["category_code"], item["category_name"])
                    for item in preview["documents"]
                },
                {
                    ("emergency_plan", "应急预案"),
                    ("drill_plan", "演练方案"),
                    ("drill_report", "应急演练报告"),
                },
            )
            self.assertEqual(preview["expected_category_count"], 12)
            self.assertEqual(preview["present_category_count"], 2)
            statuses = {
                item["category_name"]: item
                for item in preview["category_statuses"]
            }
            self.assertEqual(statuses["应急预案"]["count"], 1)
            self.assertFalse(statuses["应急预案"]["missing"])
            self.assertEqual(statuses["应急演练报告"]["count"], 1)
            self.assertFalse(statuses["应急演练报告"]["missing"])

    def test_preview_does_not_misclassify_excluded_reports_as_emergency(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "混合材料系统"
            source.mkdir()
            names = (
                "XX风险评估应急预案.docx",
                "XX符合性评测应急演练报告.docx",
                "XX应急响应报告.docx",
            )
            for name in names:
                (source / name).write_bytes(b"placeholder")

            preview = preview_source(source)
            by_name = {item["name"]: item for item in preview["documents"]}

            self.assertEqual(by_name[names[0]]["category_name"], "风险评估报告")
            self.assertEqual(by_name[names[1]]["category_name"], "符合性评测报告")
            self.assertNotIn(names[2], by_name)
            self.assertFalse(
                any(
                    item["category_code"] in {"emergency_plan", "drill_plan", "drill_report"}
                    for item in preview["documents"]
                )
            )

    def test_parse_result_combines_standard_and_emergency_failures(self) -> None:
        result = ParseResult(
            1,
            {"files": [{"source": "A.docx", "status": "failed"}]},
            Path("output"),
            {"files": [{"源文件": "B.docx", "解析状态": "failed"}]},
        )

        self.assertEqual(len(result.failed_files), 2)
        self.assertEqual(result.failed_files[0]["source"], "A.docx")
        self.assertEqual(result.failed_files[1]["源文件"], "B.docx")

    def test_project_virtualenv_is_preferred_for_parser(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            parser_root = project / "parser"
            interpreter = project / ".venv" / "bin" / "python"
            parser_root.mkdir()
            interpreter.parent.mkdir(parents=True)
            interpreter.touch()

            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("AUDIT_PARSER_PYTHON", None)
                self.assertEqual(_parser_python(parser_root), interpreter)

    def test_configured_virtualenv_python_symlink_is_not_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parser_root = root / "parser"
            base_python = root / "base-python"
            virtualenv_python = root / ".venv" / "bin" / "python"
            parser_root.mkdir()
            base_python.touch()
            virtualenv_python.parent.mkdir(parents=True)
            virtualenv_python.symlink_to(base_python)

            with patch.dict(
                os.environ,
                {"AUDIT_PARSER_PYTHON": str(virtualenv_python)},
                clear=False,
            ):
                selected = _parser_python(parser_root)

            self.assertEqual(selected, virtualenv_python)
            self.assertTrue(selected.is_symlink())

    def test_parser_startup_failure_reports_original_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "测试系统-1"
            parser_root = root / "parser"
            output = root / "output" / "parsed"
            source.mkdir()
            parser_root.mkdir()
            (source / "测试定级报告.docx").write_bytes(b"placeholder")
            script = parser_root / "parse_folder.py"
            script.write_text(
                "raise ModuleNotFoundError(\"No module named 'cv2'\")\n",
                encoding="utf-8",
            )
            script.chmod(script.stat().st_mode | stat.S_IXUSR)

            environment = {
                "AUDIT_PARSER_ROOT": str(parser_root),
                "AUDIT_PARSER_PYTHON": os.sys.executable,
            }
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(RuntimeError, "No module named 'cv2'"):
                    run_unit_parse(source, output)

            self.assertFalse(output.is_dir())

    def test_run_unit_parse_accepts_emergency_only_source_and_reports_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "应急材料系统"
            parser_root = root / "parser"
            output = root / "output" / "parsed"
            source.mkdir()
            parser_root.mkdir()
            (source / "XX应急演练总结报告.docx").write_bytes(b"placeholder")
            script = parser_root / "parse_folder.py"
            script.write_text(
                "\n".join(
                    (
                        "import json, sys",
                        "from pathlib import Path",
                        "target = Path(sys.argv[sys.argv.index('--output') + 1])",
                        "target.mkdir(parents=True)",
                        "payload = {'files': [{'源文件': 'XX应急演练总结报告.docx', '解析状态': 'failed'}]}",
                        "(target / '应急材料分析.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')",
                        "raise SystemExit(1)",
                    )
                ),
                encoding="utf-8",
            )
            events: list[dict[str, object]] = []
            environment = {
                "AUDIT_PARSER_ROOT": str(parser_root),
                "AUDIT_PARSER_PYTHON": os.sys.executable,
            }

            with patch.dict(os.environ, environment, clear=False):
                result = run_unit_parse(source, output, events.append)

            self.assertEqual(result.exit_code, 1)
            self.assertEqual(len(result.failed_files), 1)
            self.assertEqual(result.emergency_analysis["files"][0]["解析状态"], "failed")
            complete = next(item for item in events if item["type"] == "parse_complete")
            self.assertEqual(complete["failed_files"], 1)


if __name__ == "__main__":
    unittest.main()
