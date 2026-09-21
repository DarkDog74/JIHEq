from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_05 import (
    FX05AssetTableNotFoundError,
    FX05IncompleteMarkdownError,
    FX05QuantityTableNotFoundError,
    FX05SectionNotFoundError,
    extract_fx_05,
    extract_fx_05_data,
)


ASSET_MARKDOWN = """| 资产IP | 映射 IP | 资产类型 |
| --- | --- | --- |
| 10.0.0.1 | 192.168.1.1 | 服务器 |
| 10.0.0.2 |  | 虚拟机 |
"""


class FX05ExtractorTests(unittest.TestCase):
    def test_sums_only_quantity_columns_inside_exact_target_section(self) -> None:
        risk = """## 1.4.2. 系统资产
| 设备名称 | 数量 |
| --- | --- |
| 越界设备 | 99 |
## 1.4.3. 系统组件和单元资产
| 序号 | 设备名称 | 数量 | 是否虚拟 |
| --- | --- | --- | --- |
| 1 | 服务器 | 1 | 是 |
| 2 | 防火墙 | 1 | 否 |
## 1.5. 下一节
| 设备名称 | 数量 |
| --- | --- |
| 越界设备 | 88 |
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)

        self.assertEqual(result["风险评估报告资产数量"], 2)
        self.assertEqual(result["资产清单记录数"], 2)
        self.assertEqual(
            result["风险评估报告数量明细"][0]["数量值"], [1, 1]
        )

    def test_sums_multiple_quantity_tables_in_the_target_section(self) -> None:
        risk = """### 3.2. 系统组件和单元资产
| 序号 | 设备名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 1台 |

| 序号 | 设备名称 | 数量 |
| --- | --- | --- |
| 1 | 防火墙 | 1 |
### 3.3. 下一节
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)
        self.assertEqual(result["风险评估报告资产数量"], 2)
        self.assertEqual(len(result["风险评估报告数量明细"]), 2)

    def test_accepts_identification_heading_variant_when_exact_title_is_absent(self) -> None:
        risk = """## 2.3. 系统组件和单元资产识别
| 序号 | 设备名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 2 |
## 3. 下一节
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)
        self.assertEqual(result["目标章节标题"], "2.3. 系统组件和单元资产识别")
        self.assertEqual(result["风险评估报告资产数量"], 2)

    def test_recovers_numbered_target_flattened_into_pdf_table_row(self) -> None:
        risk = """1.4.3. 系统组件和单元资产................7
|1|系统单元|1.4.3. 系统组件和单元资产|说明|
|---|---|---|---|

|序号|设备名称|数量|是否虚拟|
|---|---|---|---|
|1 2 3|服务器|2|是|
|序号|系统软件类别|名称及版本|所在设备名称|
|1|操作系统|Linux|服务器|
|1.5. 评估方法|后续章节正文|||
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)
        self.assertEqual(result["目标章节定位方式"], "PDF压平编号标题")
        self.assertEqual(result["风险评估报告资产数量"], 2)

    def test_exact_flattened_title_precedes_markdown_identification_variant(self) -> None:
        risk = """|1.4.3.|系统组件和单元资产|说明|
|---|---|---|
|序号|设备名称|数量|
|1|服务器|2|
|1.5. 评估方法|后续章节正文||
## 2.3. 系统组件和单元资产识别
|序号|设备名称|数量|
|---|---|---|
|1|越界|99|
# 3. 威胁识别
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)
        self.assertEqual(result["目标章节标题"], "系统组件和单元资产")
        self.assertEqual(result["风险评估报告资产数量"], 2)

    def test_does_not_treat_longer_risk_or_value_titles_as_target_section(self) -> None:
        risk = """2.3.4. 重要程度计算 系统组件和单元资产价值根据属性赋值。
6.3.1. 系统组件和单元资产风险分析
本节出现数量 2，但不是目标资产章节。
"""
        with self.assertRaises(FX05SectionNotFoundError):
            extract_fx_05_data(risk, ASSET_MARKDOWN)

    def test_requires_a_numeric_quantity_column_in_target_section(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 序号 | 名称 | 类型 |
| --- | --- | --- |
| 1 | 应用系统 | 软件 |
## 1.5. 下一节
"""
        with self.assertRaises(FX05QuantityTableNotFoundError):
            extract_fx_05_data(risk, ASSET_MARKDOWN)

    def test_falls_back_to_counting_ip_rows_when_quantity_is_unavailable(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 资产IP | 资产类型 | 备注 |
| --- | --- | --- |
| 10.0.0.1 | 服务器 |  |
| 2409:802f:5a06:5004::2008:702 | IMS设备 |  |
| 10.0.0.1<br>10.0.0.2 | 负载均衡 | 一行有两个IP仍只计1 |
| 999.1.1.1 | 无效数据 | 不计数 |
## 1.5. 下一节
| 资产IP | 资产类型 |
| --- | --- |
| 10.0.0.99 | 越界资产 |
"""
        asset = """| 资产IP | 资产类型 |
| --- | --- |
| 10.0.0.1 | 服务器 |
| 10.0.0.2 | IMS设备 |
| 10.0.0.3 | 负载均衡 |
"""
        result = extract_fx_05_data(risk, asset)

        self.assertEqual(result["风险评估报告数量来源"], "IP行兜底")
        self.assertEqual(result["风险评估报告资产数量"], 3)
        self.assertEqual(result["风险评估报告IP行明细"][0]["IP行数"], 3)

    def test_quantity_column_takes_precedence_over_ip_row_fallback(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 资产IP | 资产类型 | 数量 |
| --- | --- | --- |
| 10.0.0.1 | 服务器 | 2 |
## 1.5. 下一节
"""
        result = extract_fx_05_data(risk, ASSET_MARKDOWN)

        self.assertEqual(result["风险评估报告数量来源"], "数量列")
        self.assertEqual(result["风险评估报告资产数量"], 2)
        self.assertEqual(result["风险评估报告IP行明细"], [])

    def test_counts_blank_ip_rows_as_asset_records(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 序号 | 名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 2 |
## 1.5. 下一节
"""
        asset = """| 资产IP | 资产类型 | 资产型号 |
| --- | --- | --- |
| 10.0.0.1 | 服务器 | M1 |
|  | 服务器 | M2 |
"""
        result = extract_fx_05_data(risk, asset)
        self.assertEqual(result["资产清单记录数"], 2)

    def test_requires_asset_inventory_table(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 序号 | 名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 1 |
"""
        with self.assertRaises(FX05AssetTableNotFoundError):
            extract_fx_05_data(risk, "没有资产表")

    def test_reads_risk_report_and_asset_inventory_as_two_inputs(self) -> None:
        risk = """### 1.4.3. 系统组件和单元资产
| 序号 | 名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 2 |
## 1.5. 下一节
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            risk_path = root / "风险评估报告.md"
            asset_path = root / "资产清单.md"
            risk_path.write_text(risk, encoding="utf-8")
            asset_path.write_text(ASSET_MARKDOWN, encoding="utf-8")
            result = extract_fx_05((risk_path, asset_path))
        self.assertEqual(result["风险评估报告资产数量"], 2)

    def test_explicit_parse_error_stub_is_reported_as_parse_failure(self) -> None:
        with self.assertRaisesRegex(FX05IncompleteMarkdownError, "显式解析失败存根"):
            extract_fx_05_data(
                "<!-- PARSE_ERROR source=broken.docx -->\n",
                ASSET_MARKDOWN,
            )


if __name__ == "__main__":
    unittest.main()
