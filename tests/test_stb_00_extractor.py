from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.stb_00 import (
    STB00ExtractionError,
    _is_sync_report_list_file,
    extract_stb_00,
    extract_stb_00_data,
)


SANTONG = """中国移动通信集团浙江有限公司互联网公有云服务平台融合边缘云平台三同步情况报告

我公司  融合边缘云平台  ，年度新增（扩建，改建）新增1773台硬件设备 ，均已同步配套相关网络安全措施。

特此报告！

公司名称（盖章）：

中国移动通信集团浙江有限公司网管中心

2025年  6 月 5  日
"""


def _write_manifest(directory: str, manifest: dict) -> Path:
    parsed = Path(directory) / "parsed"
    parsed.mkdir(parents=True, exist_ok=True)
    manifest_path = parsed / "三同步.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


def _write_analysis(parsed: Path, sources: list[str]) -> None:
    (parsed / "文件分析.json").write_text(
        json.dumps(
            {"files": [{"source": source} for source in sources]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class STB00ReportListNameTests(unittest.TestCase):
    def test_non_contiguous_ordered_keywords_match_excel(self) -> None:
        self.assertTrue(
            _is_sync_report_list_file(
                "材料/三同步报告新建、改建、扩建设备清单-某系统.xlsx"
            )
        )
        self.assertTrue(_is_sync_report_list_file("三 同步-报告（设备）清单.XLS"))

    def test_extension_temp_file_and_keyword_order_are_strict(self) -> None:
        self.assertFalse(_is_sync_report_list_file("~$三同步报告清单.xlsx"))
        self.assertFalse(_is_sync_report_list_file("三同步报告清单.docx"))
        self.assertFalse(_is_sync_report_list_file("三同步清单.xlsx"))
        self.assertFalse(_is_sync_report_list_file("报告清单-三同步.xlsx"))


class STB00ExtractorDataTests(unittest.TestCase):
    def test_word_report_reads_markdown_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {
                    "document_type": "三同步报告",
                    "source_file": "某某三同步情况报告.docx",
                    "text_file": "三同步.md",
                    "image_files": [],
                },
            )
            (manifest_path.parent / "三同步.md").write_text(SANTONG, encoding="utf-8")

            fields, images = extract_stb_00_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertIn("三同步情况报告", fields["三同步正文"])
        self.assertEqual(fields["图片数量"], 0)
        self.assertEqual(fields["源文件"], "某某三同步情况报告.docx")
        self.assertTrue(fields["三同步报告存在"])
        self.assertFalse(fields["三同步报告清单存在"])
        self.assertEqual(fields["三同步报告清单文件"], [])
        self.assertEqual(images, [])

    def test_report_list_is_read_from_existing_file_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {
                    "source_file": "某系统三同步情况报告.docx",
                    "text_file": "三同步.md",
                    "image_files": [],
                },
            )
            (manifest_path.parent / "三同步.md").write_text(SANTONG, encoding="utf-8")
            matched = "原始材料/三同步报告新建、改建、扩建设备清单-某系统.xlsx"
            _write_analysis(
                manifest_path.parent,
                [
                    "某系统三同步情况报告.docx",
                    matched,
                    "~$三同步报告清单.xlsx",
                    "三同步设备清单.xlsx",
                ],
            )

            fields, _ = extract_stb_00_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertTrue(fields["三同步报告清单存在"])
        self.assertEqual(fields["三同步报告清单文件"], [matched])

    def test_image_report_collects_images_without_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {
                    "document_type": "三同步报告",
                    "source_file": "某某三同步情况报告.jpg",
                    "text_file": None,
                    "image_files": ["三同步.png", "三同步-002.png"],
                },
            )
            for name in ("三同步.png", "三同步-002.png"):
                (manifest_path.parent / name).write_bytes(b"png")

            fields, images = extract_stb_00_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertEqual(fields["三同步正文"], "")
        self.assertEqual(fields["图片数量"], 2)
        self.assertEqual([path.name for path in images], ["三同步.png", "三同步-002.png"])

    def test_mixed_report_collects_text_and_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {
                    "text_file": "三同步.md",
                    "image_files": ["三同步.png"],
                },
            )
            (manifest_path.parent / "三同步.md").write_text(SANTONG, encoding="utf-8")
            (manifest_path.parent / "三同步.png").write_bytes(b"png")

            fields, images = extract_stb_00_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertIn("三同步情况报告", fields["三同步正文"])
        self.assertEqual(len(images), 1)

    def test_missing_pointees_raise(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory, {"text_file": "不存在.md", "image_files": ["不存在.png"]}
            )
            with self.assertRaises(STB00ExtractionError):
                extract_stb_00_data(
                    json.loads(manifest_path.read_text(encoding="utf-8")),
                    manifest_path.parent,
                )


class STB00ExtractorWrapperTests(unittest.TestCase):
    def test_wrapper_stages_images_and_writes_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {
                    "source_file": "某某三同步情况报告.jpg",
                    "text_file": None,
                    "image_files": ["三同步.png"],
                },
            )
            (manifest_path.parent / "三同步.png").write_bytes(b"png")

            fields = extract_stb_00(manifest_path)

            staged_dir = manifest_path.parent.parent / "audits" / "STB-00"
            payload = json.loads(
                (staged_dir / "STB-00.json").read_text(encoding="utf-8")
            )
            self.assertEqual(fields["图片数量"], 1)
            self.assertEqual(len(fields["图片文件"]), 1)
            self.assertEqual(
                fields["图片文件"][0]["相对路径"], "audits/STB-00/三同步.png"
            )
            self.assertTrue((staged_dir / "三同步.png").is_file())
            self.assertEqual(
                payload["原始输入图片"][0]["复制状态"], "已复制"
            )


if __name__ == "__main__":
    unittest.main()
