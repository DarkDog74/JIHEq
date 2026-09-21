from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from audit_rules import ylbg_05 as rule_mod
from core.batch_process import format_batch_summary, run_batch
from core.model_client import ModelReply


def _reply(has_watermark: bool) -> ModelReply:
    return ModelReply(
        text=json.dumps(
            {
                "有时间水印": has_watermark,
                "水印文字": "2025-05-21 14:30" if has_watermark else "",
                "水印位置": "右下" if has_watermark else "",
                "依据": "有时间叠字" if has_watermark else "未见时间水印",
            },
            ensure_ascii=False,
        ),
        model="fake-vision",
        elapsed_ms=2,
    )


def _write_unit(root: Path, name: str, image_name: str | None) -> None:
    unit = root / name
    unit.mkdir()
    if image_name is None:
        return
    (unit / "应急演练报告.md").write_text(
        f'# 应急演练报告\n# 九、演练过程\n[[IMAGE id=IMG-001 file="附件/应急演练报告/{image_name}"]]\n', encoding="utf-8"
    )
    asset_dir = unit / "附件" / "应急演练报告"
    asset_dir.mkdir(parents=True)
    (asset_dir / image_name).write_bytes(b"image")
    manifest = {
        "files": [
            {
                "用途": "应急演练报告",
                "解析状态": "success",
                "标准Markdown": "应急演练报告.md",
                "图片": [
                    {
                        "marker_id": "IMG-001",
                        "media_type": "image/png",
                        "file": f"附件/应急演练报告/{image_name}",
                        "status": "exported",
                    }
                ],
            }
        ]
    }
    (unit / "应急材料分析.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8"
    )


class YLBG05BatchTests(unittest.TestCase):
    def test_batch_reports_pass_fail_missing_and_missing_image_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            _write_unit(dataset, "fail", "no-watermark.png")
            _write_unit(dataset, "missing", None)
            _write_unit(dataset, "pass", "watermarked.png")
            _write_unit(dataset, "no-section", "unused.png")
            (dataset / "no-section" / "应急演练报告.md").write_text('# 报告\n无目标章节', encoding='utf-8')

            def fake_model(_prompt, image_paths, **_kwargs):
                return _reply(Path(image_paths[0]).name != "no-watermark.png")

            with patch.object(rule_mod, "_chat_with_images", side_effect=fake_model):
                summary = run_batch(
                    dataset_dir=dataset,
                    item_id="YLBG-05",
                    output_dir=root / "output",
                    unit_depth=1,
                    overwrite=True,
                )

            self.assertEqual(
                summary["status_counts"], {"missing_document": 1, "missing_target_section": 1, "success": 2}
            )
            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 3, "not_applicable": 0},
            )
            terminal = format_batch_summary(summary, root / "output")
            self.assertIn("no-watermark.png", terminal)
            self.assertIn("缺少时间水印", terminal)


if __name__ == "__main__":
    unittest.main()
