from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main as main_module


class RawToAuditSmokeTests(unittest.TestCase):
    def test_raw_office_files_are_parsed_before_rule_audit(self) -> None:
        """离线贯通：原始 Word/Excel -> anydoc Markdown -> YLP-01 -> 总汇总。"""

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source_root = root / "核心网部"
            system_dir = source_root / "测试系统"
            fake_modules = root / "fake_modules"
            output_root = root / "全流程结果"
            system_dir.mkdir(parents=True)
            fake_modules.mkdir()

            raw_word = system_dir / "测试系统定级报告.docx"
            raw_excel = system_dir / "2026年网络信息安全应急演练计划-7月.xlsx"
            raw_word.write_bytes(b"fake docx payload")
            raw_excel.write_bytes(b"fake xlsx payload")

            (fake_modules / "anydoc.py").write_text(
                "from types import SimpleNamespace\n"
                "def format_from_bytes(data):\n"
                "    return 'xlsx' if data.startswith(b'fake xlsx') else 'docx'\n"
                "def format_from_path(path):\n"
                "    return path.suffix.lower().lstrip('.')\n"
                "def to_document(data, detected):\n"
                "    return SimpleNamespace(assets=[], blocks=[], notes=[])\n"
                "def to_markdown_bytes(data, detected):\n"
                "    if detected == 'xlsx':\n"
                "        return (\n"
                "            '# 基础安全演练计划\\n\\n'\n"
                "            '| 归属单位 | 参演系统 | 恶意程序类 | 网络攻击类 | 安全隐患类 | 计划演练时间 |\\n'\n"
                "            '|---|---|---|---|---|---|\\n'\n"
                "            '| 核心网部 | 测试系统 | √ |  |  | 2026年7月 |\\n'\n"
                "        )\n"
                "    return '# 测试系统定级报告\\n\\n离线冒烟测试正文。\\n'\n",
                encoding="utf-8",
            )

            original_pythonpath = os.environ.get("PYTHONPATH", "")
            pythonpath = os.pathsep.join(
                value for value in (str(fake_modules), original_pythonpath) if value
            )
            with patch.dict(os.environ, {"PYTHONPATH": pythonpath}):
                result = main_module.run_source_batch_all(
                    source_dir=source_root,
                    output_dir=output_root,
                    unit_depth=1,
                    rule_only=True,
                )

            parsed_unit = output_root / "解析结果" / "测试系统"
            grading_markdown = parsed_unit / "定级报告.md"
            plan_markdown = parsed_unit / "演练计划.md"
            self.assertTrue(raw_word.is_file())
            self.assertTrue(raw_excel.is_file())
            self.assertTrue(grading_markdown.is_file())
            self.assertTrue(plan_markdown.is_file())
            self.assertIn("离线冒烟测试正文", grading_markdown.read_text(encoding="utf-8"))
            self.assertIn("√", plan_markdown.read_text(encoding="utf-8"))

            system_summary_path = (
                output_root / "稽核结果" / "测试系统" / "系统稽核汇总.json"
            )
            system_summary = json.loads(system_summary_path.read_text(encoding="utf-8"))
            ylp_item = next(
                item
                for item in system_summary["audit"]["items"]
                if item["item_id"] == "YLP-01"
            )
            self.assertEqual(ylp_item["status"], "completed")
            self.assertTrue(ylp_item["batch_passed"])
            self.assertEqual(ylp_item["audit_counts"]["passed"], 1)
            self.assertEqual(result["parse_counts"], {"success": 1, "partial": 0, "error": 0})

            ylp_result_path = (
                output_root
                / "稽核结果"
                / "测试系统"
                / "YLP-01"
                / "稽核结果.json"
            )
            ylp_result = json.loads(ylp_result_path.read_text(encoding="utf-8"))
            self.assertTrue(ylp_result["passed"])
            self.assertFalse(
                (output_root / "稽核结果" / "测试系统" / "YLP-01" / "YLP-01").exists()
            )
            self.assertTrue(system_summary_path.is_file())
            self.assertTrue((output_root / "稽核结果" / "全部系统汇总.json").is_file())
            self.assertTrue((output_root / "全流程汇总.json").is_file())


if __name__ == "__main__":
    unittest.main()
