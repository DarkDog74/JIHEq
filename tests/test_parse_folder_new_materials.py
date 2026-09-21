from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER = PROJECT_ROOT / "parser" / "parse_folder.py"
PARSER_PYTHON = PROJECT_ROOT / ".venv" / "bin" / "python"


class ParseFolderNewMaterialsTests(unittest.TestCase):
    def test_conformity_report_is_not_classified_as_table_or_proof(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            output = root / "output"
            source.mkdir()
            for name in (
                "系统符合性评测表.xlsx",
                "系统符合性评测报告.docx",
                "系统符合性评测报告证明材料.docx",
                "证明材料.docx",
                "系统定级报告资产表.xlsx",
            ):
                (source / name).write_bytes(b"test")

            completed = subprocess.run(
                [
                    str(PARSER_PYTHON if PARSER_PYTHON.is_file() else Path(sys.executable)),
                    str(PARSER),
                    str(source),
                    "--output",
                    str(output),
                    "--analysis-only",
                ],
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            analysis = json.loads((output / "文件分析.json").read_text(encoding="utf-8"))
            categories = {
                item["source"]: (item["category_code"], item["category_name"])
                for item in analysis["files"]
            }
            self.assertEqual(
                categories["系统符合性评测表.xlsx"],
                ("2.5", "符合性评测表"),
            )
            self.assertEqual(
                categories["系统符合性评测报告.docx"],
                ("2.9", "符合性评测报告"),
            )
            self.assertEqual(
                categories["系统符合性评测报告证明材料.docx"],
                ("2.6", "符合性评测表证明材料"),
            )
            self.assertEqual(
                categories["证明材料.docx"],
                ("2.6", "符合性评测表证明材料"),
            )
            self.assertEqual(
                categories["系统定级报告资产表.xlsx"],
                ("2.3", "资产清单"),
            )


if __name__ == "__main__":
    unittest.main()
