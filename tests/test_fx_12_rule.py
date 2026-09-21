from __future__ import annotations

import unittest
from itertools import combinations

from audit_rules.fx_12 import audit_fx_12


def fields(*matched_types: str, readable: int = 1) -> dict[str, object]:
    types = ("基线", "漏扫", "渗透测试")
    evidence = [
        {"材料类型": material_type, "字段": "任务名称", "命中内容": material_type, "源文件": material_type + ".zip"}
        for material_type in matched_types
    ]
    abnormal = (
        []
        if readable
        else [{"源文件": "扫描.zip", "状态": "ZIP内无HTML", "异常原因": "ZIP压缩包中不存在HTML文件"}]
    )
    return {
        "扫描报告压缩包数": max(1, len(matched_types)),
        "可读取HTML压缩包数": max(1, len(matched_types)) if readable else 0,
        "类型存在性": {item: item in matched_types for item in types},
        "识别材料类型": list(matched_types),
        "类型证据": evidence,
        "异常压缩包": abnormal,
        "压缩包检查结果": [{"源文件": t + ".zip"} for t in matched_types] or [{"源文件": "扫描.zip"}],
    }


class FX12RuleTests(unittest.TestCase):
    def test_all_three_material_types_pass(self) -> None:
        result = audit_fx_12(fields("基线", "漏扫", "渗透测试"))
        self.assertTrue(result["passed"])
        self.assertEqual(result["missing_types"], [])
        self.assertEqual(len(set(result["material_files"].values())), 3)

    def test_any_missing_type_fails(self) -> None:
        types = ("基线", "漏扫", "渗透测试")
        for count in (0, 1, 2):
            for present in combinations(types, count):
                with self.subTest(present=present):
                    result = audit_fx_12(fields(*present))
                    self.assertFalse(result["passed"])
                    self.assertEqual(result["missing_types"], [t for t in types if t not in present])

    def test_same_file_cannot_count_as_three_files(self) -> None:
        data = fields("基线", "漏扫", "渗透测试")
        for item in data["类型证据"]:
            item["源文件"] = "基线.zip"
        result = audit_fx_12(data)
        self.assertFalse(result["passed"])
        self.assertIn("三份独立文件", result["message"])

    def test_evidence_without_known_source_cannot_pass(self) -> None:
        data = fields("基线", "漏扫", "渗透测试")
        data["类型证据"][-1]["源文件"] = "未知.zip"
        self.assertFalse(audit_fx_12(data)["passed"])

    def test_three_types_with_fewer_readable_files_cannot_pass(self) -> None:
        data = fields("基线", "漏扫", "渗透测试")
        data["可读取HTML压缩包数"] = 2
        self.assertFalse(audit_fx_12(data)["passed"])

    def test_more_than_three_files_can_pass(self) -> None:
        data = fields("基线", "漏扫", "渗透测试")
        data["扫描报告压缩包数"] = data["可读取HTML压缩包数"] = 4
        data["压缩包检查结果"].append({"源文件": "额外漏扫.zip"})
        data["类型证据"].insert(0, {"材料类型": "漏扫", "源文件": "额外漏扫.zip"})
        self.assertTrue(audit_fx_12(data)["passed"])

    def test_no_recognized_type_fails(self) -> None:
        result = audit_fx_12(fields())

        self.assertFalse(result["passed"])
        self.assertIn("HTML标识字段未识别", result["message"])

    def test_no_readable_html_reports_specific_reason(self) -> None:
        result = audit_fx_12(fields(readable=0))

        self.assertFalse(result["passed"])
        self.assertIn("没有可读取的HTML", result["issues"][0])

    def test_rejects_presence_without_evidence(self) -> None:
        invalid = fields("基线")
        invalid["类型证据"] = []
        with self.assertRaisesRegex(ValueError, "缺少对应证据"):
            audit_fx_12(invalid)


if __name__ == "__main__":
    unittest.main()
