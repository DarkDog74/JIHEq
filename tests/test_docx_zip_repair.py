from __future__ import annotations

import io
import struct
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER_ROOT = PROJECT_ROOT / "parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

from docx_zip_repair import (  # noqa: E402
    repair_truncated_docx_bytes,
    repair_truncated_empty_zip_comment,
)


REQUIRED_PARTS = (
    "[Content_Types].xml",
    "_rels/.rels",
    "word/document.xml",
)
EOCD_LENGTH = 22
EOCD_DISK_NUMBER_OFFSET = 4
EOCD_CENTRAL_DIRECTORY_SIZE_OFFSET = 12


def _minimal_docx_bytes(*, omit: str | None = None) -> bytes:
    parts = {
        "[Content_Types].xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            b'<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            b'<Default Extension="xml" ContentType="application/xml"/>'
            b'<Override PartName="/word/document.xml" '
            b'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            b"</Types>"
        ),
        "_rels/.rels": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            b'<Relationship Id="rId1" '
            b'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
            b'Target="word/document.xml"/>'
            b"</Relationships>"
        ),
        "word/document.xml": (
            b'<?xml version="1.0" encoding="UTF-8"?>'
            b'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            b"<w:body><w:p><w:r><w:t>DOCX_REPAIR_CRC_MARKER</w:t></w:r></w:p></w:body>"
            b"</w:document>"
        ),
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as package:
        for name, payload in parts.items():
            if name != omit:
                package.writestr(name, payload)
    result = buffer.getvalue()
    if len(result) < EOCD_LENGTH or result[-EOCD_LENGTH:-EOCD_LENGTH + 4] != b"PK\x05\x06":
        raise AssertionError("测试DOCX应包含无注释的标准ZIP结束记录")
    return result


def _mutate_eocd_uint16(payload: bytes, relative_offset: int, value: int) -> bytes:
    mutated = bytearray(payload)
    struct.pack_into("<H", mutated, len(mutated) - EOCD_LENGTH + relative_offset, value)
    return bytes(mutated)


def _mutate_eocd_uint32(payload: bytes, relative_offset: int, value: int) -> bytes:
    mutated = bytearray(payload)
    struct.pack_into("<I", mutated, len(mutated) - EOCD_LENGTH + relative_offset, value)
    return bytes(mutated)


class DocxZipRepairTests(unittest.TestCase):
    def assert_not_repaired(self, original: bytes) -> None:
        result = repair_truncated_docx_bytes(original)

        self.assertEqual(result.data, original)
        self.assertFalse(result.repaired)
        self.assertEqual(result.added_bytes, 0)

    def test_valid_docx_is_returned_unchanged(self) -> None:
        original = _minimal_docx_bytes()

        result = repair_truncated_docx_bytes(original)

        self.assertEqual(result.data, original)
        self.assertFalse(result.repaired)
        self.assertEqual(result.added_bytes, 0)
        self.assertFalse(result.warning)

    def test_repairs_docx_missing_one_eocd_byte(self) -> None:
        complete = _minimal_docx_bytes()

        result = repair_truncated_docx_bytes(complete[:-1])

        self.assertEqual(result.data, complete)
        self.assertTrue(result.repaired)
        self.assertEqual(result.added_bytes, 1)
        self.assertTrue(result.warning)
        with zipfile.ZipFile(io.BytesIO(result.data)) as package:
            self.assertIsNone(package.testzip())
            self.assertTrue(set(REQUIRED_PARTS).issubset(package.namelist()))

    def test_repairs_docx_missing_two_eocd_bytes(self) -> None:
        complete = _minimal_docx_bytes()

        result = repair_truncated_docx_bytes(complete[:-2])

        self.assertEqual(result.data, complete)
        self.assertTrue(result.repaired)
        self.assertEqual(result.added_bytes, 2)
        self.assertTrue(result.warning)

    def test_rejects_docx_missing_three_eocd_bytes(self) -> None:
        complete = _minimal_docx_bytes()
        self.assert_not_repaired(complete[:-3])

    def test_rejects_split_archive(self) -> None:
        complete = _minimal_docx_bytes()
        split_archive = _mutate_eocd_uint16(
            complete,
            EOCD_DISK_NUMBER_OFFSET,
            1,
        )

        self.assert_not_repaired(split_archive[:-2])

    def test_rejects_incoherent_central_directory_bounds(self) -> None:
        complete = _minimal_docx_bytes()
        eocd_offset = len(complete) - EOCD_LENGTH
        central_directory_size = struct.unpack_from(
            "<I",
            complete,
            eocd_offset + EOCD_CENTRAL_DIRECTORY_SIZE_OFFSET,
        )[0]
        incoherent = _mutate_eocd_uint32(
            complete,
            EOCD_CENTRAL_DIRECTORY_SIZE_OFFSET,
            central_directory_size + 1,
        )

        self.assert_not_repaired(incoherent[:-2])

    def test_rejects_docx_missing_a_required_part(self) -> None:
        for missing_part in REQUIRED_PARTS:
            with self.subTest(missing_part=missing_part):
                incomplete_package = _minimal_docx_bytes(omit=missing_part)
                self.assert_not_repaired(incomplete_package[:-2])

    def test_rejects_crc_corruption(self) -> None:
        complete = _minimal_docx_bytes()
        marker = b"DOCX_REPAIR_CRC_MARKER"
        marker_offset = complete.find(marker)
        self.assertGreaterEqual(marker_offset, 0)
        corrupted = bytearray(complete)
        corrupted[marker_offset] ^= 0x01

        self.assert_not_repaired(bytes(corrupted[:-2]))

    def test_path_api_does_not_modify_original_file(self) -> None:
        complete = _minimal_docx_bytes()
        truncated = complete[:-1]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.docx"
            destination = root / "repaired.docx"
            source.write_bytes(truncated)

            returned = repair_truncated_empty_zip_comment(source, destination)

            self.assertEqual(returned, destination.resolve())
            self.assertEqual(source.read_bytes(), truncated)
            self.assertEqual(destination.read_bytes(), complete)
            with zipfile.ZipFile(destination) as package:
                self.assertIsNone(package.testzip())


if __name__ == "__main__":
    unittest.main()
