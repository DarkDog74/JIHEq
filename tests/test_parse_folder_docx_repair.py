from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER = PROJECT_ROOT / "parser" / "parse_folder.py"
PARSER_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


def _write_minimal_docx(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", "<Types/>")
        package.writestr("_rels/.rels", "<Relationships/>")
        package.writestr("word/document.xml", "<document><body/></document>")


class ParseFolderDocxRepairTests(unittest.TestCase):
    def test_risk_report_is_repaired_in_memory_before_anydoc(self) -> None:
        for missing_bytes in (1, 2):
            with self.subTest(missing_bytes=missing_bytes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / "source"
                output = root / "output"
                fake_modules = root / "fake_modules"
                source.mkdir()
                fake_modules.mkdir()
                docx = source / "测试系统_信息安全风险评估报告.docx"
                pdf = source / "测试系统_信息安全风险评估报告.pdf"
                _write_minimal_docx(docx)
                complete = docx.read_bytes()
                self.assertEqual(complete[-2:], b"\x00\x00")
                original = complete[:-missing_bytes]
                docx.write_bytes(original)
                pdf.write_bytes(b"%PDF-1.4\n")

                (fake_modules / "anydoc.py").write_text(
                    "from io import BytesIO\n"
                    "from types import SimpleNamespace\n"
                    "import zipfile\n"
                    "def _verify(data):\n"
                    "    with zipfile.ZipFile(BytesIO(data)) as package:\n"
                    "        assert package.testzip() is None\n"
                    "        assert {'[Content_Types].xml', '_rels/.rels', 'word/document.xml'} <= set(package.namelist())\n"
                    "def format_from_bytes(data):\n"
                    "    _verify(data)\n"
                    "    return 'docx'\n"
                    "def format_from_path(path):\n"
                    "    return path.suffix.lower().lstrip('.')\n"
                    "def to_document(data, detected):\n"
                    "    _verify(data)\n"
                    "    assert detected == 'docx'\n"
                    "    return SimpleNamespace(assets=[], blocks=[], notes=[])\n"
                    "def to_markdown_bytes(data, detected):\n"
                    "    _verify(data)\n"
                    "    return '# 已修复风险评估报告\\n'\n",
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
                        "2.7",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )

                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                self.assertEqual(docx.read_bytes(), original)
                self.assertIn("已修复风险评估报告", (output / "风险评估报告.md").read_text())
                analysis = json.loads((output / "文件分析.json").read_text(encoding="utf-8"))
                records = {item["extension"]: item for item in analysis["files"]}
                self.assertEqual(records[".docx"]["status"], "success")
                self.assertTrue(
                    any(
                        f"缺少末尾{missing_bytes}个字节" in warning
                        and "原文件未修改" in warning
                        for warning in records[".docx"]["warnings"]
                    )
                )
                self.assertEqual(records[".pdf"]["status"], "duplicate_skipped")


if __name__ == "__main__":
    unittest.main()
