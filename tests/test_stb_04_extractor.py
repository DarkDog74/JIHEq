from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.stb_04 import STB04ExtractionError, extract_stb_04, extract_stb_04_data


def _write_manifest(directory: str, manifest: dict) -> Path:
    parsed = Path(directory) / "parsed"
    parsed.mkdir(parents=True, exist_ok=True)
    manifest_path = parsed / "三同步.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return manifest_path


class STB04ExtractorDataTests(unittest.TestCase):
    def test_word_report_has_no_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {"source_file": "三同步情况报告.docx", "text_file": "三同步.md",
                 "image_files": []},
            )
            (manifest_path.parent / "三同步.md").write_text("正文", encoding="utf-8")

            fields, images = extract_stb_04_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertFalse(fields["has_images"])
        self.assertEqual(fields["报告形态"], "仅Markdown")
        self.assertEqual(images, [])

    def test_image_report_collects_images(self) -> None:
        # 真实案例（2026-08-26 私有云平台-3）：三同步报告是 JPG，
        # 清单 text_file=null、image_files=["三同步.jpg"]
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {"source_file": "三同步报告.jpg", "text_file": None,
                 "image_files": ["三同步.jpg"]},
            )
            (manifest_path.parent / "三同步.jpg").write_bytes(b"jpg")

            fields, images = extract_stb_04_data(
                json.loads(manifest_path.read_text(encoding="utf-8")),
                manifest_path.parent,
            )

        self.assertTrue(fields["has_images"])
        self.assertEqual(fields["报告形态"], "图片")
        self.assertEqual([path.name for path in images], ["三同步.jpg"])

    def test_manifest_pointing_to_nothing_raises(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory, {"text_file": "不存在.md", "image_files": ["不存在.jpg"]}
            )
            with self.assertRaises(STB04ExtractionError):
                extract_stb_04_data(
                    json.loads(manifest_path.read_text(encoding="utf-8")),
                    manifest_path.parent,
                )


class STB04ExtractorWrapperTests(unittest.TestCase):
    def test_wrapper_stages_image_and_writes_fields(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = _write_manifest(
                directory,
                {"source_file": "三同步报告.jpg", "text_file": None,
                 "image_files": ["三同步.jpg"]},
            )
            (manifest_path.parent / "三同步.jpg").write_bytes(b"jpg")

            fields = extract_stb_04(manifest_path)

            staged_dir = manifest_path.parent.parent / "audits" / "STB-04"
            payload = json.loads(
                (staged_dir / "STB-04.json").read_text(encoding="utf-8")
            )
            self.assertTrue(fields["has_images"])
            self.assertEqual(fields["图片文件"][0]["相对路径"], "audits/STB-04/三同步.jpg")
            self.assertTrue((staged_dir / "三同步.jpg").is_file())
            self.assertEqual(payload["原始输入图片"][0]["复制状态"], "已复制")


if __name__ == "__main__":
    unittest.main()
