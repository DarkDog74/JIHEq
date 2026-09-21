from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors.fx_12 import FX12ExtractionError, extract_fx_12, extract_fx_12_json


def payload(*matched_types: str) -> dict[str, object]:
    types = ("基线", "漏扫", "渗透测试")
    evidence = [
        {
            "源文件": "扫描/report.zip",
            "入口HTML": "index.html",
            "材料类型": material_type,
            "字段": "任务类型",
            "命中内容": material_type,
        }
        for material_type in matched_types
    ]
    return {
        "扫描报告压缩包数": 1,
        "可读取HTML压缩包数": 1,
        "识别材料类型": list(matched_types),
        "类型存在性": {item: item in matched_types for item in types},
        "类型证据": evidence,
        "异常压缩包": [],
        "压缩包检查结果": [{"源文件": "扫描/report.zip", "状态": "已识别"}],
    }


class FX12ExtractorTests(unittest.TestCase):
    def test_extracts_only_required_scan_archive_facts(self) -> None:
        result = extract_fx_12_json(payload("基线"))

        self.assertEqual(result["扫描报告压缩包数"], 1)
        self.assertTrue(result["类型存在性"]["基线"])
        self.assertEqual(len(result["类型证据"]), 1)

    def test_reads_scan_report_json_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "扫描报告.json"
            path.write_text(
                json.dumps(payload("漏扫"), ensure_ascii=False), encoding="utf-8"
            )

            result = extract_fx_12(path)

            self.assertEqual(result["识别材料类型"], ["漏扫"])

    def test_rejects_inconsistent_archive_counts(self) -> None:
        value = payload("漏扫")
        value["可读取HTML压缩包数"] = 2

        with self.assertRaisesRegex(FX12ExtractionError, "不能超过"):
            extract_fx_12_json(value)


if __name__ == "__main__":
    unittest.main()
