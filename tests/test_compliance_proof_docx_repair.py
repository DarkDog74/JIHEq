from __future__ import annotations

import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER_ROOT = PROJECT_ROOT / "parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from compliance_proof_adapter import ComplianceProofParseError, parse_compliance_proof
from docx_zip_repair import (
    TRUNCATED_EOCD_REPAIR_WARNING,
    has_truncated_empty_zip_comment,
    repair_truncated_empty_zip_comment,
)


def _write_minimal_docx(path: Path) -> None:
    content_types_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
"""
    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>证明材料正文</w:t></w:r></w:p>
  </w:body>
</w:document>
"""
    relationships_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""
    document_relationships_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("[Content_Types].xml", content_types_xml)
        package.writestr("_rels/.rels", relationships_xml)
        package.writestr("word/document.xml", document_xml)
        package.writestr("word/_rels/document.xml.rels", document_relationships_xml)


def _truncate_comment_length(path: Path) -> None:
    payload = path.read_bytes()
    if payload[-2:] != b"\x00\x00":
        raise AssertionError("测试DOCX应以空ZIP注释长度结尾")
    path.write_bytes(payload[:-2])


class ComplianceProofDocxRepairTests(unittest.TestCase):
    def test_repairs_only_the_temporary_copy_and_parses_end_to_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "符合性测评表证明材料.docx"
            output = root / "output"
            _write_minimal_docx(source)
            _truncate_comment_length(source)
            original_size = source.stat().st_size

            self.assertFalse(zipfile.is_zipfile(source))
            self.assertTrue(has_truncated_empty_zip_comment(source))

            result = parse_compliance_proof(source, output, "符合性测评表证明材料")

            self.assertIn("证明材料正文", result.markdown)
            self.assertIn(TRUNCATED_EOCD_REPAIR_WARNING, result.warnings)
            self.assertEqual(source.stat().st_size, original_size)
            self.assertFalse(zipfile.is_zipfile(source))

    def test_repaired_copy_is_a_valid_zip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "broken.docx"
            repaired = root / "repaired.docx"
            _write_minimal_docx(source)
            _truncate_comment_length(source)

            repair_truncated_empty_zip_comment(source, repaired)

            self.assertTrue(zipfile.is_zipfile(repaired))
            with zipfile.ZipFile(repaired) as package:
                self.assertIsNone(package.testzip())
                self.assertIn("word/document.xml", package.namelist())

    def test_other_corruption_is_not_silently_repaired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "actually-broken.docx"
            source.write_bytes(b"PK\x03\x04incomplete")

            self.assertFalse(has_truncated_empty_zip_comment(source))
            with self.assertRaises(ComplianceProofParseError) as caught:
                parse_compliance_proof(source, root / "output", "proof")

            self.assertEqual(caught.exception.failure_type, "docx_corrupt")


if __name__ == "__main__":
    unittest.main()
