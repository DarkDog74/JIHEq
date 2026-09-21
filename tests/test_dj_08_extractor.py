from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.dj_08 import DJ08ExtractionError, extract_dj_08, extract_dj_08_text
from extractors.dj_09 import DJ09ExtractionError


REPORT_WITH_IMAGE = """# 定级报告

## 3. 技术情况

- 2\) **网络拓扑结构**

[[IMAGE id=IMG-002 file="附件/定级报告/IMG-002.png" name="IMG-002" alt="" type="image/png"]] ![IMG-002](<附件/定级报告/IMG-002.png>)

- 3\) **硬件设备部署情况**

通过核心交换机实现路由功能。

附：定级报告资产信息：

| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 网络位置 | 责任人 |
| --- | --- | --- | --- | --- | --- |
| 10.0.0.1 | 防火墙 | FW1 | 厂商A | 内网 | 张三 |
| 10.0.0.1 | 防火墙 | FW1 | 厂商A | 内网 | 张三 |
| 10.0.0.2 | 服务器 | S1 | 厂商B | 内网 | 李四 |
"""

REPORT_WITHOUT_IMAGE = """# 定级报告

## 3. 技术情况

- 2\) **网络拓扑结构**

（拓扑图缺失，仅有文字描述）

| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 网络位置 | 责任人 |
| --- | --- | --- | --- | --- | --- |
| 10.0.0.1 | 防火墙 | FW1 | 厂商A | 内网 | 张三 |
"""


class DJ08ExtractorTests(unittest.TestCase):
    def test_extracts_topology_image_and_deduped_ips(self) -> None:
        result = extract_dj_08_text(REPORT_WITH_IMAGE)
        self.assertEqual(result["拓扑图"]["id"], "IMG-002")
        self.assertEqual(result["拓扑图"]["文件"], "附件/定级报告/IMG-002.png")
        self.assertEqual(result["拓扑图"]["所在小节"], "技术情况 / 网络拓扑结构")
        self.assertEqual(result["资产IP清单"], ["10.0.0.1", "10.0.0.2"])
        self.assertEqual(result["资产数量"], 3)
        self.assertNotIn("资产表", result)

    def test_rejects_topology_section_without_image(self) -> None:
        with self.assertRaisesRegex(DJ08ExtractionError, "拓扑图"):
            extract_dj_08_text(REPORT_WITHOUT_IMAGE)

    def test_rejects_report_without_asset_table(self) -> None:
        report = REPORT_WITH_IMAGE.split("附：")[0]
        with self.assertRaises(DJ09ExtractionError):
            extract_dj_08_text(report)

    def test_wrapper_copies_topology_image_into_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            image = parsed / "附件" / "定级报告" / "IMG-002.png"
            image.parent.mkdir(parents=True)
            image.write_bytes(b"fake-png")
            report = parsed / "定级报告.md"
            report.write_text(REPORT_WITH_IMAGE, encoding="utf-8")

            extract_dj_08(report)

            staged = root / "audits" / "DJ-08"
            payload = json.loads((staged / "DJ-08.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["输入文档"], ["parsed/定级报告.md"])
            self.assertEqual(
                payload["原始输入图片"],
                [
                    {
                        "id": "IMG-002",
                        "说明": "定级报告网络拓扑图",
                        "原始路径": "parsed/附件/定级报告/IMG-002.png",
                        "audits内文件": "IMG-002.png",
                        "复制状态": "已复制",
                    }
                ],
            )
            self.assertEqual((staged / "IMG-002.png").read_bytes(), b"fake-png")


if __name__ == "__main__":
    unittest.main()
