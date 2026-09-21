from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARSER_ROOT = PROJECT_ROOT / "parser"
if str(PARSER_ROOT) not in sys.path:
    sys.path.insert(0, str(PARSER_ROOT))

import emergency_document_adapter as adapter


class _FakeAnydoc(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("anydoc")
        self.calls: list[tuple[bytes, str]] = []

    def to_document(self, data: bytes, source_format: str) -> object:
        self.calls.append((data, source_format))
        return object()


class EmergencyDocumentAdapterTests(unittest.TestCase):
    def _parse_with_fake_anydoc(
        self,
        source: Path,
        output: Path,
        *,
        drill: bool = False,
        converted: bytes | None = None,
    ) -> tuple[adapter.EmergencyMarkdownResult, _FakeAnydoc]:
        fake_anydoc = _FakeAnydoc()
        rendered = SimpleNamespace(
            occurrences=[
                {
                    "marker_id": "IMG-001",
                    "file": "附件/测试/IMG-001.png",
                    "location": "body[1]",
                }
            ]
        )
        conversion_patch = patch.object(
            adapter,
            "convert_doc_to_docx_bytes",
            return_value=(converted, ["旧版Word已转换"]),
        )
        with (
            patch.dict(sys.modules, {"anydoc": fake_anydoc}),
            patch.object(adapter, "export_assets", return_value={1: object()}),
            patch.object(adapter, "render_document", return_value=rendered),
            patch.object(
                adapter,
                "render_anydoc_with_image_markers",
                return_value=("# 应急文档\n\n[[IMAGE id=IMG-001]]\n", ["图片告警"]),
            ),
            conversion_patch,
        ):
            parser_function = (
                adapter.parse_emergency_drill if drill else adapter.parse_emergency_plan
            )
            result = parser_function(source, output, stem="测试")
        return result, fake_anydoc

    def test_docx_plan_reuses_anydoc_and_preserves_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "网络安全应急预案.docx"
            source.write_bytes(b"docx payload")

            result, fake_anydoc = self._parse_with_fake_anydoc(source, root / "output")

        self.assertEqual(result.document_kind, "应急预案")
        self.assertEqual(result.source_format, "docx")
        self.assertEqual(result.conversion_method, "anydoc_docx")
        self.assertEqual(result.asset_count, 1)
        self.assertEqual(result.asset_directory, "附件/测试")
        self.assertEqual(result.images[0]["marker_id"], "IMG-001")
        self.assertIn("[[IMAGE", result.markdown)
        self.assertEqual(result.warnings, ["图片告警"])
        self.assertEqual(fake_anydoc.calls, [(b"docx payload", "docx")])

    def test_legacy_doc_drill_uses_converted_docx_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "应急演练方案.doc"
            source.write_bytes(b"legacy payload")

            result, fake_anydoc = self._parse_with_fake_anydoc(
                source,
                root / "output",
                drill=True,
                converted=b"converted docx",
            )

        self.assertEqual(result.document_kind, "应急演练")
        self.assertEqual(result.source_format, "doc")
        self.assertEqual(result.conversion_method, "libreoffice_then_anydoc")
        self.assertEqual(result.warnings, ["旧版Word已转换", "图片告警"])
        self.assertEqual(fake_anydoc.calls, [(b"converted docx", "docx")])

    def test_rejects_wps_and_pdf_without_invoking_anydoc(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for filename in ("应急预案.wps", "应急预案.pdf"):
                with self.subTest(filename=filename):
                    source = root / filename
                    source.write_bytes(b"unsupported")
                    with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                        adapter.parse_emergency_plan(source, root / "output")
                    self.assertEqual(caught.exception.failure_type, "unsupported_format")

    def test_reports_legacy_doc_conversion_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "应急演练方案.doc"
            source.write_bytes(b"legacy payload")
            with patch.object(
                adapter,
                "convert_doc_to_docx_bytes",
                return_value=(None, ["未找到LibreOffice"]),
            ):
                with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                    adapter.parse_emergency_drill(source, root / "output")

        self.assertEqual(caught.exception.failure_type, "legacy_doc_conversion_failed")
        self.assertIn("未找到LibreOffice", caught.exception.technical_detail)

    def test_rejects_missing_and_empty_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(adapter.EmergencyDocumentParseError) as missing:
                adapter.parse_emergency_plan(root / "missing.docx", root / "output")
            self.assertEqual(missing.exception.failure_type, "source_not_found")

            empty = root / "empty.docx"
            empty.touch()
            with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                adapter.parse_emergency_plan(empty, root / "output")
            self.assertEqual(caught.exception.failure_type, "empty_source")

    def test_rejects_empty_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "应急预案.docx"
            source.write_bytes(b"docx payload")
            fake_anydoc = _FakeAnydoc()
            rendered = SimpleNamespace(occurrences=[])
            with (
                patch.dict(sys.modules, {"anydoc": fake_anydoc}),
                patch.object(adapter, "export_assets", return_value={}),
                patch.object(adapter, "render_document", return_value=rendered),
                patch.object(
                    adapter,
                    "render_anydoc_with_image_markers",
                    return_value=("  \n", []),
                ),
            ):
                with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                    adapter.parse_emergency_plan(source, root / "output")

        self.assertEqual(caught.exception.failure_type, "empty_markdown")

    def test_writes_markdown_and_requires_explicit_overwrite(self) -> None:
        result = adapter.EmergencyMarkdownResult(
            document_kind="应急预案",
            source_format="docx",
            conversion_method="anydoc_docx",
            markdown="# 应急预案\n",
            asset_count=0,
            asset_directory=None,
            images=[],
            warnings=[],
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "nested" / "应急预案.md"
            adapter.write_emergency_markdown(result, output)
            self.assertEqual(output.read_text(encoding="utf-8"), result.markdown)

            with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                adapter.write_emergency_markdown(result, output)
            self.assertEqual(caught.exception.failure_type, "output_exists")

            adapter.write_emergency_markdown(result, output, overwrite=True)

    def test_same_stem_requires_overwrite_and_replaces_all_old_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "应急演练总结.docx"
            output = root / "output"
            source.write_bytes(b"docx payload")
            fake_anydoc = _FakeAnydoc()
            asset_counts = iter((5, 2))

            def fake_export(_document: object, target: Path, stem: str) -> dict[int, object]:
                count = next(asset_counts)
                asset_dir = target / "附件" / stem
                asset_dir.mkdir(parents=True)
                assets: dict[int, object] = {}
                for number in range(1, count + 1):
                    (asset_dir / f"IMG-{number:03d}.png").write_bytes(b"image")
                    assets[number] = object()
                return assets

            def fake_render(_document: object, assets: dict[int, object]) -> object:
                return SimpleNamespace(
                    occurrences=[{"marker_id": f"IMG-{number:03d}"} for number in assets]
                )

            with (
                patch.dict(sys.modules, {"anydoc": fake_anydoc}),
                patch.object(adapter, "export_assets", side_effect=fake_export),
                patch.object(adapter, "render_document", side_effect=fake_render),
                patch.object(
                    adapter,
                    "render_anydoc_with_image_markers",
                    return_value=("# 应急演练总结\n", []),
                ),
            ):
                first = adapter.parse_emergency_drill(source, output, stem="应急演练")
                self.assertEqual(first.asset_count, 5)

                with self.assertRaises(adapter.EmergencyDocumentParseError) as caught:
                    adapter.parse_emergency_drill(source, output, stem="应急演练")
                self.assertEqual(caught.exception.failure_type, "output_exists")

                second = adapter.parse_emergency_drill(
                    source,
                    output,
                    stem="应急演练",
                    overwrite=True,
                )

            asset_files = sorted(
                path.name for path in (output / "附件" / "应急演练").iterdir()
            )
            self.assertEqual(second.asset_count, 2)
            self.assertEqual(asset_files, ["IMG-001.png", "IMG-002.png"])


if __name__ == "__main__":
    unittest.main()
