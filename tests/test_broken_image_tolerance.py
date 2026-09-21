from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = PROJECT_ROOT / "parser" / "document_parsers" / "风险评估报告" / "source.py"


def _load_source_module():
    spec = importlib.util.spec_from_file_location("risk_source_broken_image_test", SOURCE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_docx_with_missing_image(path: Path) -> None:
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>正文保留</w:t></w:r><w:r><a:blip r:embed="rIdBroken"/></w:r></w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rIdBroken"
    Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
    Target="media/missing.png"/>
</Relationships>
"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("word/document.xml", document_xml)
        package.writestr("word/_rels/document.xml.rels", relationships_xml)


class BrokenImageToleranceTests(unittest.TestCase):
    def test_default_mode_still_rejects_missing_image(self) -> None:
        source = _load_source_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "broken.docx"
            _write_docx_with_missing_image(docx)
            with self.assertRaises(source.SourceError):
                source.extract_ooxml(docx, root / "assets", "assets")

    def test_tolerant_mode_skips_only_missing_image(self) -> None:
        source = _load_source_module()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            docx = root / "broken.docx"
            _write_docx_with_missing_image(docx)
            structure = source.extract_ooxml(
                docx,
                root / "assets",
                "assets",
                skip_broken_images=True,
            )

        self.assertEqual(structure.blocks[0].text, "正文保留")
        self.assertEqual(structure.images, [])
        self.assertEqual(len(structure.warnings), 1)
        self.assertIn("已跳过失效图片", structure.warnings[0])
        self.assertIn("word/media/missing.png", structure.warnings[0])


if __name__ == "__main__":
    unittest.main()
