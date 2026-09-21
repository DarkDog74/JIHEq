from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from audit_rules.fx_07 import audit_fx_07
from core.batch_process import format_batch_summary, run_batch
from extractors.fx_05 import FX05SectionNotFoundError
from extractors.fx_07 import FX07IncompleteMarkdownError, extract_fx_07_documents


Asset = tuple[str, str, str]


def asset_table(
    *assets: Asset, headers: tuple[str, str, str] = ("资产IP", "资产类型", "资产型号")
) -> str:
    rows = [
        f"| {headers[0]} | {headers[1]} | {headers[2]} | 设备厂商 |",
        "| --- | --- | --- | --- |",
    ]
    rows.extend(f"| {ip} | {asset_type} | {model} | 厂商A |" for ip, asset_type, model in assets)
    return "\n".join(rows)


def risk_document(
    *assets: Asset,
    headers: tuple[str, str, str] = ("资产IP", "资产类型", "资产型号"),
    after_section: str = "",
) -> str:
    return "\n".join(
        (
            "### 1.4.3. 系统组件和单元资产",
            asset_table(*assets, headers=headers),
            "## 1.5. 下一节",
            after_section,
        )
    )


def grading_document(
    *assets: Asset,
    headers: tuple[str, str, str] = ("资产IP", "资产类型", "资产型号"),
    before_appendix: str = "",
) -> str:
    return "\n".join(
        (
            "# 定级报告",
            before_appendix,
            "附：定级报告资产信息：",
            asset_table(*assets, headers=headers),
        )
    )


def rule_fields(
    risk: list[dict[str, str]],
    grading: list[dict[str, str]],
    *,
    risk_incomplete: list[dict[str, object]] | None = None,
    grading_incomplete: list[dict[str, object]] | None = None,
    risk_missing: list[str] | None = None,
    grading_missing: list[str] | None = None,
) -> dict[str, object]:
    return {
        "风险评估报告资产记录": risk,
        "定级报告资产记录": grading,
        "风险评估报告不完整资产记录": risk_incomplete or [],
        "定级报告不完整资产记录": grading_incomplete or [],
        "风险评估报告资产表缺失列": risk_missing or [],
        "定级报告资产表缺失列": grading_missing or [],
    }


def record(ip: str, asset_type: str, model: str) -> dict[str, str]:
    return {"IP": ip, "类型": asset_type, "型号": model}


class FX07Tests(unittest.TestCase):
    def extract(self, risk_text: str, grading_text: str) -> dict[str, object]:
        with tempfile.TemporaryDirectory() as directory:
            risk = Path(directory) / "风险评估报告.md"
            grading = Path(directory) / "定级报告.md"
            risk.write_text(risk_text, encoding="utf-8")
            grading.write_text(grading_text, encoding="utf-8")
            return extract_fx_07_documents(risk, grading)

    def test_extracts_three_target_columns_and_evidence(self) -> None:
        assets = (("10.0.0.1", "服务器", "M1"), ("10.0.0.2", "防火墙", "F1"))
        fields = self.extract(risk_document(*assets), grading_document(*assets))

        self.assertEqual(fields["风险评估报告资产数量"], 2)
        self.assertEqual(fields["定级报告资产数量"], 2)
        self.assertEqual(
            fields["风险评估报告资产记录"][0],
            {"IP": "10.0.0.1", "类型": "服务器", "型号": "M1", "行号": 4},
        )
        self.assertEqual(fields["风险评估报告资产表缺失列"], [])
        self.assertEqual(
            fields["风险评估报告资产表证据"][0]["列名"],
            {"IP": "资产IP", "类型": "资产类型", "型号": "资产型号"},
        )

    def test_header_aliases_match_by_keywords(self) -> None:
        asset = ("2409:ABCD::1", "交换机", "S5735")
        headers = ("私网Ip地址", "设备类型", "品牌及型号")
        fields = self.extract(
            risk_document(asset, headers=headers),
            grading_document(asset, headers=headers),
        )

        self.assertEqual(
            fields["风险评估报告资产记录"][0],
            {"IP": "2409:abcd::1", "类型": "交换机", "型号": "S5735", "行号": 4},
        )
        self.assertTrue(audit_fx_07(fields)["passed"])

    def test_prefers_exact_columns_when_multiple_keyword_columns_exist(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 私网IP地址 | 资产IP | 设备类型 | 资产类型 | 品牌及型号 | 资产型号 |
| --- | --- | --- | --- | --- | --- |
| 192.0.2.1 | 10.0.0.1 | 误类型 | 服务器 | 误型号 | M1 |
## 1.5. 下一节
"""
        fields = self.extract(
            risk, grading_document(("10.0.0.1", "服务器", "M1"))
        )
        self.assertTrue(audit_fx_07(fields)["passed"])

    def test_risk_search_is_limited_to_target_section(self) -> None:
        asset = ("10.0.0.1", "服务器", "M1")
        duplicate = asset_table(("10.0.0.9", "路由器", "R9"))
        fields = self.extract(
            risk_document(asset, after_section=duplicate), grading_document(asset)
        )
        self.assertEqual(fields["风险评估报告资产数量"], 1)
        self.assertTrue(audit_fx_07(fields)["passed"])

    def test_order_difference_passes(self) -> None:
        first = record("10.0.0.1", "服务器", "M1")
        second = record("10.0.0.2", "防火墙", "F1")
        result = audit_fx_07(rule_fields([first, second], [second, first]))

        self.assertTrue(result["passed"])
        self.assertTrue(result["quantity_match"])
        self.assertEqual(result["field_mismatch_counts"], {"IP": 0, "类型": 0, "型号": 0})

    def test_ipv6_expanded_and_compressed_forms_are_format_equivalent(self) -> None:
        result = audit_fx_07(
            rule_fields(
                [record("2409:ABCD:0:0:0:0:0:1", "服务器", "M1")],
                [record("2409:abcd::1", "服务器", "M1")],
            )
        )

        self.assertTrue(result["passed"])

    def test_ip_difference_fails(self) -> None:
        result = audit_fx_07(
            rule_fields(
                [record("10.0.0.1", "服务器", "M1")],
                [record("10.0.0.2", "服务器", "M1")],
            )
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["field_mismatch_counts"]["IP"], 1)
        self.assertFalse(result["ip_match"])

    def test_type_difference_fails(self) -> None:
        result = audit_fx_07(
            rule_fields(
                [record("10.0.0.1", "服务器", "M1")],
                [record("10.0.0.1", "数据库", "M1")],
            )
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["field_mismatch_counts"]["类型"], 1)
        self.assertFalse(result["type_match"])

    def test_model_difference_fails(self) -> None:
        result = audit_fx_07(
            rule_fields(
                [record("10.0.0.1", "服务器", "M1")],
                [record("10.0.0.1", "服务器", "M2")],
            )
        )
        self.assertFalse(result["passed"])
        self.assertEqual(result["field_mismatch_counts"]["型号"], 1)
        self.assertFalse(result["model_match"])

    def test_models_swapped_between_same_ips_fail_despite_equal_model_counter(self) -> None:
        result = audit_fx_07(
            rule_fields(
                [
                    record("10.0.0.1", "服务器", "M1"),
                    record("10.0.0.2", "服务器", "M2"),
                ],
                [
                    record("10.0.0.1", "服务器", "M2"),
                    record("10.0.0.2", "服务器", "M1"),
                ],
            )
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["field_mismatch_counts"]["型号"], 2)
        self.assertFalse(result["model_match"])

    def test_duplicate_records_must_have_same_multiplicity(self) -> None:
        repeated = record("10.0.0.1", "服务器", "M1")
        other = record("10.0.0.2", "服务器", "M2")
        passed = audit_fx_07(
            rule_fields([repeated, other, repeated], [repeated, repeated, other])
        )
        failed = audit_fx_07(
            rule_fields([repeated, repeated, other], [repeated, other, other])
        )

        self.assertTrue(passed["passed"])
        self.assertFalse(failed["passed"])
        self.assertEqual(len(failed["differences"]["仅风险评估报告存在"]), 1)
        self.assertEqual(len(failed["differences"]["仅定级报告存在"]), 1)

    def test_extra_record_makes_each_dimension_multiset_different(self) -> None:
        repeated = record("10.0.0.1", "服务器", "M1")
        result = audit_fx_07(
            rule_fields([repeated], [repeated, repeated])
        )

        self.assertFalse(result["quantity_match"])
        self.assertFalse(result["ip_match"])
        self.assertFalse(result["type_match"])
        self.assertFalse(result["model_match"])

    def test_missing_target_column_is_rule_failure_not_extraction_error(self) -> None:
        incomplete_headers = ("资产IP", "资产类型", "设备厂商")
        fields = self.extract(
            risk_document(("10.0.0.1", "服务器", "华为"), headers=incomplete_headers),
            grading_document(("10.0.0.1", "服务器", "M1")),
        )
        result = audit_fx_07(fields)

        self.assertEqual(fields["风险评估报告资产表缺失列"], ["型号"])
        self.assertFalse(result["passed"])
        self.assertIn("缺少同行表头列：型号", result["issues"][0])

    def test_blank_target_cell_is_collected_and_fails(self) -> None:
        fields = self.extract(
            risk_document(("10.0.0.1", "服务器", "")),
            grading_document(("10.0.0.1", "服务器", "M1")),
        )
        result = audit_fx_07(fields)

        incomplete = fields["风险评估报告不完整资产记录"]
        self.assertEqual(len(incomplete), 1)
        self.assertEqual(incomplete[0]["缺失字段"], ["型号"])
        self.assertFalse(result["passed"])
        self.assertIn("IP/类型/型号未填完整", result["issues"][0])

    def test_requires_target_section_in_risk_report(self) -> None:
        with self.assertRaises(FX05SectionNotFoundError):
            self.extract("# 风险评估报告\n\n没有目标章节", grading_document(("10.0.0.1", "服务器", "M1")))

    def test_rejects_parse_error_stub(self) -> None:
        with self.assertRaisesRegex(FX07IncompleteMarkdownError, "定级报告.md"):
            self.extract(
                risk_document(("10.0.0.1", "服务器", "M1")),
                "<!-- PARSE_ERROR source=broken.doc -->\n",
            )

    def test_batch_reports_pass_fail_missing_and_missing_column_as_rule_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "dataset"
            passed = dataset / "部门A" / "一致"
            failed = dataset / "部门A" / "型号不一致"
            missing = dataset / "部门A" / "缺少风险报告"
            missing_column = dataset / "部门A" / "定级报告缺列"
            for unit in (passed, failed, missing, missing_column):
                unit.mkdir(parents=True)
            base = ("10.0.0.1", "服务器", "M1")
            (passed / "风险评估报告.md").write_text(
                risk_document(base), encoding="utf-8"
            )
            (passed / "定级报告.md").write_text(
                grading_document(base), encoding="utf-8"
            )
            (failed / "风险评估报告.md").write_text(
                risk_document(base), encoding="utf-8"
            )
            (failed / "定级报告.md").write_text(
                grading_document(("10.0.0.1", "服务器", "M2")), encoding="utf-8"
            )
            (missing / "定级报告.md").write_text(
                grading_document(base), encoding="utf-8"
            )
            (missing_column / "风险评估报告.md").write_text(
                risk_document(base), encoding="utf-8"
            )
            (missing_column / "定级报告.md").write_text(
                grading_document(
                    ("10.0.0.1", "服务器", "华为"),
                    headers=("资产IP", "资产类型", "设备厂商"),
                ),
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(
                dataset_dir=dataset, item_id="FX-07", output_dir=output
            )
            terminal = format_batch_summary(summary, output)

        self.assertIn("[FX-07] ✅ 通过 | 部门A/一致", terminal)
        self.assertIn("[FX-07] ❌ 不通过 | 部门A/型号不一致", terminal)
        self.assertIn("[FX-07] ❌ 不通过 | 部门A/缺少风险报告 | 原因：缺少目标文档", terminal)
        self.assertIn("[FX-07] ❌ 不通过 | 部门A/定级报告缺列", terminal)
        self.assertIn("IP=一致 | 类型=一致 | 型号=不一致", terminal)
        self.assertEqual(summary["status_counts"].get("extraction_error", 0), 0)
        missing_column_run = next(
            run for run in summary["runs"] if run["unit"] == "部门A/定级报告缺列"
        )
        self.assertEqual(missing_column_run["status"], "success")
        self.assertEqual(missing_column_run["classification"], "failed")


if __name__ == "__main__":
    unittest.main()
