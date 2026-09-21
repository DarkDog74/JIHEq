from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.zm_01 import ZM01ExtractionError, extract_zm_01, extract_zm_01_text


PROOF = """# 证明材料

23-01-01-01-01 检查项：应具有数据敏感度的界定标准。

敏感数据界定遵循业界规范。

[[IMAGE id=IMG-001 file="附件/证明材料/IMG-001.png" name="IMG-001" alt="截图1" type="image/png"]]

23-01-01-02-01 检查项：数据产生时，应根据数据的敏感度进行分类。

本项无截图。

[[IMAGE id=IMG-002 file="附件/证明材料/IMG-002.png" name="IMG-002" alt="截图2" type="image/png"]]

23-01-02-01-01 检查项：鉴别信息传输保密性。

采用 SSH 加密传输。
"""


class ZM01ExtractorTests(unittest.TestCase):
    def test_extracts_items_with_screenshots(self) -> None:
        result = extract_zm_01_text(PROOF)
        self.assertEqual(result["检查项数量"], 3)
        self.assertEqual(result["含截图检查项数量"], 2)
        self.assertEqual(result["无截图检查项编号"], ["23-01-02-01-01"])

        first = result["检查项"][0]
        self.assertEqual(first["检查项编号"], "23-01-01-01-01")
        self.assertIn("敏感度", first["说明文字"])
        self.assertEqual(first["截图"][0]["id"], "IMG-001")

    def test_rejects_document_without_item_codes(self) -> None:
        with self.assertRaisesRegex(ZM01ExtractionError, "检查项编号"):
            extract_zm_01_text("# 证明材料\n\n没有任何编号。")

    def test_wrapper_copies_unique_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            attachments = parsed / "附件" / "证明材料"
            attachments.mkdir(parents=True)
            (attachments / "IMG-001.png").write_bytes(b"png-1")
            (attachments / "IMG-002.png").write_bytes(b"png-2")
            proof = parsed / "符合性评测表证明材料.md"
            proof.write_text(PROOF, encoding="utf-8")

            extract_zm_01(proof)

            staged = root / "audits" / "ZM-01"
            payload = json.loads((staged / "ZM-01.json").read_text(encoding="utf-8"))
            self.assertEqual(
                [image["audits内文件"] for image in payload["原始输入图片"]],
                ["IMG-001.png", "IMG-002.png"],
            )
            self.assertTrue((staged / "IMG-001.png").is_file())
            self.assertTrue((staged / "IMG-002.png").is_file())


if __name__ == "__main__":
    unittest.main()
