from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.fx_06 import FX06ExtractionError, extract_fx_06, extract_fx_06_data


RISK_REPORT = """# 风险评估报告

## 1.4 评估对象

被评估系统为融合边缘云平台。

[[IMAGE id=IMG-001 file="附件/风险评估报告/IMG-001.png" name="PDF第12页拓扑图证据" alt="PDF第12页拓扑图证据" type="image/png"]]
[[IMAGE id=IMG-002 file="附件/风险评估报告/IMG-002.png" name="PDF第13页拓扑图证据" alt="PDF第13页拓扑图证据" type="image/png"]]

## 1.5 评估方法

按标准流程评估。
"""

INVENTORY = """## Sheet

| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 网络位置 | 定级对象名称 | 责任人 |
| --- | --- | --- | --- | --- | --- | --- |
| 10.0.0.1 | 防火墙 | FW1 | 厂商A | 内网 | 融合边缘云平台 | 张三 |
| 10.0.0.2 | 服务器 | S1 | 厂商B | 内网 | 融合边缘云平台 | 李四 |
"""

LIST_LABEL_RISK_REPORT = """# 风险评估报告

## 1.4.3. 系统组件和单元资产

- 拓扑图

电路域核心交换网络拓扑

[[IMAGE id=IMG-001 file="附件/风险评估报告/IMG-001.png" alt=""]]

NPMSC网络拓扑

[[IMAGE id=IMG-002 file="附件/风险评估报告/IMG-002.png" alt=""]]

## 1.5.1. 评估流程

[[IMAGE id=IMG-003 file="附件/风险评估报告/IMG-003.png" alt="评估流程图"]]
"""


class FX06ExtractorTests(unittest.TestCase):
    def test_extracts_images_with_section_and_inventory_ips(self) -> None:
        result = extract_fx_06_data(RISK_REPORT, INVENTORY)
        self.assertEqual(
            [image["id"] for image in result["报告图片"]], ["IMG-001", "IMG-002"]
        )
        self.assertEqual(result["报告图片"][0]["所在章节"], "1.4 评估对象")
        self.assertEqual(result["报告图片"][0]["alt"], "PDF第12页拓扑图证据")
        self.assertEqual(
            [image["id"] for image in result["拓扑图"]], ["IMG-001", "IMG-002"]
        )
        self.assertEqual(result["资产IP清单"], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(result["资产数量"], 2)

    def test_list_label_selects_only_images_before_next_heading(self) -> None:
        result = extract_fx_06_data(LIST_LABEL_RISK_REPORT, INVENTORY)

        self.assertEqual(
            [image["id"] for image in result["报告图片"]],
            ["IMG-001", "IMG-002", "IMG-003"],
        )
        self.assertEqual(
            [image["id"] for image in result["拓扑图"]], ["IMG-001", "IMG-002"]
        )
        self.assertEqual(result["拓扑图"][0]["定位文字"], "- 拓扑图")
        self.assertEqual(result["拓扑图"][0]["定位行号"], 5)

    def test_rejects_report_without_images(self) -> None:
        with self.assertRaisesRegex(FX06ExtractionError, "未找到任何图片"):
            extract_fx_06_data("# 风险评估报告\n\n纯文本报告。", INVENTORY)

    def test_wrapper_copies_all_selected_topology_images(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            parsed.mkdir()
            attachments = parsed / "附件" / "风险评估报告"
            attachments.mkdir(parents=True)
            (attachments / "IMG-001.png").write_bytes(b"png-1")
            (attachments / "IMG-002.png").write_bytes(b"png-2")
            risk = parsed / "风险评估报告.md"
            risk.write_text(RISK_REPORT, encoding="utf-8")
            inventory = parsed / "资产清单.md"
            inventory.write_text(INVENTORY, encoding="utf-8")

            extract_fx_06((risk, inventory))

            staged = root / "audits" / "FX-06"
            payload = json.loads((staged / "FX-06.json").read_text(encoding="utf-8"))
            self.assertEqual(
                payload["输入文档"], ["parsed/风险评估报告.md", "parsed/资产清单.md"]
            )
            self.assertEqual(
                [image["audits内文件"] for image in payload["原始输入图片"]],
                ["IMG-001.png", "IMG-002.png"],
            )
            self.assertEqual((staged / "IMG-001.png").read_bytes(), b"png-1")
            self.assertEqual((staged / "IMG-002.png").read_bytes(), b"png-2")

    def test_wrapper_rejects_single_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FX06ExtractionError):
                extract_fx_06(Path(directory) / "风险评估报告.md")

    def test_wrapper_stages_only_images_in_topology_text_range(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            attachments = parsed / "附件" / "风险评估报告"
            attachments.mkdir(parents=True)
            for index in range(1, 4):
                (attachments / f"IMG-{index:03d}.png").write_bytes(f"png-{index}".encode())
            risk = parsed / "风险评估报告.md"
            risk.write_text(LIST_LABEL_RISK_REPORT, encoding="utf-8")
            inventory = parsed / "资产清单.md"
            inventory.write_text(INVENTORY, encoding="utf-8")

            fields = extract_fx_06((risk, inventory))

            self.assertEqual(
                [image["id"] for image in fields["图片文件"]],
                ["IMG-001", "IMG-002"],
            )
            staged = root / "audits" / "FX-06"
            self.assertTrue((staged / "IMG-001.png").is_file())
            self.assertTrue((staged / "IMG-002.png").is_file())
            self.assertFalse((staged / "IMG-003.png").exists())


if __name__ == "__main__":
    unittest.main()
