from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.common import audit_artifact_scope
from extractors.errors import TargetSectionNotFoundError
from extractors.emergency_audit_common import EmergencyAuditExtractionError
from extractors.ylbg_05 import extract_ylbg_05, _select_process_images


class YLBG05ExtractorTests(unittest.TestCase):
    def _write_manifest(self, root: Path, current_images: list[dict]) -> Path:
        references = [f'[[IMAGE id={x.get("marker_id", "")} file="{x.get("file", "")}"]]' for x in current_images]
        (root / "应急演练报告.md").write_text(
            "# 应急演练报告\n# 九、演练过程\n" + "\n".join(references), encoding="utf-8"
        )
        (root / "上一份应急演练报告.md").write_text(
            "# 上一份应急演练报告\n", encoding="utf-8"
        )
        payload = {
            "files": [
                {
                    "用途": "应急演练报告",
                    "解析状态": "success",
                    "标准Markdown": "应急演练报告.md",
                    "图片": current_images,
                },
                {
                    "用途": "应急演练报告",
                    "解析状态": "success",
                    "标准Markdown": "上一份应急演练报告.md",
                    "图片": [
                        {
                            "marker_id": "OLD-001",
                            "media_type": "image/png",
                            "file": "附件/上一份应急演练报告/old.png",
                            "status": "exported",
                        }
                    ],
                },
            ]
        }
        manifest = root / "应急材料分析.json"
        manifest.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return manifest

    def test_collects_only_current_real_images_in_manifest_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_dir = root / "附件" / "应急演练报告"
            asset_dir.mkdir(parents=True)
            (asset_dir / "first.jpg").write_bytes(b"first")
            (asset_dir / "second.png").write_bytes(b"second")
            manifest = self._write_manifest(
                root,
                [
                    {
                        "marker_id": "IMG-003",
                        "media_type": "image/jpeg",
                        "file": "附件/应急演练报告/first.jpg",
                        "name": "现场照片A",
                        "status": "exported",
                    },
                    {
                        "marker_id": "IMG-004",
                        "media_type": "image/png",
                        "file": "附件/应急演练报告/second.png",
                        "name": "现场照片B",
                        "status": "exported",
                    },
                    {
                        "kind": "image",
                        "marker_id": "IMG-005",
                        "media_type": "application/vnd.ms-ole-object",
                        "file": "附件/应急演练报告/object.bin",
                        "status": "exported",
                    },
                ],
            )

            with audit_artifact_scope(root / "audits" / "YLBG-05"):
                fields = extract_ylbg_05(manifest)

            self.assertEqual(fields["图片数量"], 2)
            self.assertEqual(fields["可核验图片数量"], 2)
            self.assertEqual(
                [item["图片ID"] for item in fields["图片清单"]],
                ["IMG-003", "IMG-004"],
            )
            self.assertEqual(fields["非图片嵌入对象数量"], 1)
            self.assertEqual(len(fields["图片文件"]), 2)
            self.assertTrue(
                all(Path(item["路径"]).is_file() for item in fields["图片文件"])
            )
            self.assertNotIn(
                "OLD-001", [item["图片ID"] for item in fields["图片清单"]]
            )

    def test_missing_or_unsupported_image_is_kept_as_uncheckable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            asset_dir = root / "附件" / "应急演练报告"
            asset_dir.mkdir(parents=True)
            (asset_dir / "unsupported.gif").write_bytes(b"gif")
            manifest = self._write_manifest(
                root,
                [
                    {
                        "marker_id": "IMG-001",
                        "media_type": "image/jpeg",
                        "file": "附件/应急演练报告/missing.jpg",
                        "status": "exported",
                    },
                    {
                        "marker_id": "IMG-002",
                        "media_type": "image/gif",
                        "file": "附件/应急演练报告/unsupported.gif",
                        "status": "exported",
                    },
                ],
            )

            with audit_artifact_scope(root / "audits" / "YLBG-05"):
                fields = extract_ylbg_05(manifest)

            self.assertEqual(fields["图片数量"], 2)
            self.assertEqual(fields["可核验图片数量"], 0)
            self.assertFalse(fields["图片清单"][0]["可核验"])
            self.assertIn("不存在", fields["图片清单"][0]["无法核验原因"])
            self.assertFalse(fields["图片清单"][1]["可核验"])
            self.assertIn("暂不支持", fields["图片清单"][1]["无法核验原因"])
            self.assertNotIn("图片文件", fields)

    def test_external_or_unavailable_image_is_not_misclassified_as_non_image(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = self._write_manifest(
                root,
                [
                    {
                        "kind": "image",
                        "marker_id": "IMG-EXT-001",
                        "media_type": None,
                        "file": "https://example.invalid/photo.png",
                        "status": "external",
                    },
                    {
                        "kind": "image",
                        "marker_id": "IMG-MISSING-001",
                        "media_type": None,
                        "file": "",
                        "status": "unavailable",
                    },
                ],
            )

            with audit_artifact_scope(root / "audits" / "YLBG-05"):
                fields = extract_ylbg_05(manifest)

            self.assertEqual(fields["图片数量"], 2)
            self.assertEqual(fields["可核验图片数量"], 0)
            self.assertEqual(fields["非图片嵌入对象数量"], 0)
            self.assertEqual(
                [item["图片ID"] for item in fields["图片清单"]],
                ["IMG-EXT-001", "IMG-MISSING-001"],
            )
            self.assertTrue(
                all(not item["可核验"] for item in fields["图片清单"])
            )
            self.assertIn("external", fields["图片清单"][0]["无法核验原因"])
            self.assertIn("unavailable", fields["图片清单"][1]["无法核验原因"])


class YLBG05ScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.record = {"图片": [
            {"marker_id": f"IMG-{i}", "file": f"附件/{i}.png", "media_type": "image/png", "status": "exported"}
            for i in range(1, 4)
        ]}

    def test_keeps_all_later_sections_and_multiple_images_on_one_line(self) -> None:
        text = ('# 网络拓扑\n[[IMAGE id=IMG-1 file="附件/1.png"]]\n'
                '# 九、演练过程\n[[IMAGE id=IMG-2 file="附件/2.png"]] ![图](<附件/2.png>)\n'
                '# 十三、演练现场照片记录\n[[IMAGE id=IMG-3 file="附件/3.png"]] [[IMAGE id=IMG-2 file="附件/2.png"]]')
        selected, scope = _select_process_images(text, self.record)
        self.assertEqual([r['marker_id'] for r in selected['图片']], ['IMG-2', 'IMG-3'])
        self.assertEqual(scope['范围外图片数量'], 1)

    def test_numbers_bold_plain_titles_and_standard_images(self) -> None:
        for title in ('# 五、演练过程', '## 9. **演练过程**', '九、演练过程', '# 演练过程'):
            with self.subTest(title=title):
                record = {"图片": self.record['图片'][1:]}
                selected, _ = _select_process_images(title + '\n![图](<附件/2.png>)\n# 总结\n![图](附件/3.png)', record)
                self.assertEqual(len(selected['图片']), 2)

    def test_toc_and_body_mentions_do_not_start_scope(self) -> None:
        for text in ('# 目录\n[九、演练过程](#_Toc123)\n# 网络拓扑\n', '# 总结\n本报告演练过程见附件。', '九、演练过程........12'):
            with self.subTest(text=text), self.assertRaises(TargetSectionNotFoundError):
                _select_process_images(text, {"图片": []})

    def test_duplicate_body_titles_are_ambiguous(self) -> None:
        with self.assertRaises(EmergencyAuditExtractionError):
            _select_process_images('# 演练过程\n正文\n# 演练过程\n正文', {"图片": []})

    def test_no_images_after_start_does_not_restore_topology(self) -> None:
        selected, scope = _select_process_images('![图](附件/1.png)\n# 演练过程\n无图', {"图片": self.record['图片'][:1]})
        self.assertEqual(selected['图片'], [])
        self.assertEqual(scope['范围外图片数量'], 1)

    def test_unregistered_reference_is_kept_as_uncheckable(self) -> None:
        selected, _ = _select_process_images('# 演练过程\n![未登记](附件/missing.png)', {"图片": []})
        self.assertEqual(len(selected['图片']), 1)
        self.assertEqual(selected['图片'][0]['status'], 'unavailable')

    def test_unlocated_manifest_image_does_not_silently_disappear(self) -> None:
        with self.assertRaisesRegex(EmergencyAuditExtractionError, '没有位置'):
            _select_process_images('# 演练过程\n正文', self.record)

    def test_heading_line_only_keeps_images_after_title(self) -> None:
        text = '# [[IMAGE id=IMG-1 file="附件/1.png"]] 九、演练过程 [[IMAGE id=IMG-2 file="附件/2.png"]]'
        selected, _ = _select_process_images(text, {"图片": self.record['图片'][:2]})
        self.assertEqual([r['marker_id'] for r in selected['图片']], ['IMG-2'])


if __name__ == "__main__":
    unittest.main()
