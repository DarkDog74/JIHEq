from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors import (
    MultipleRequiredDocumentsError,
    RequiredDocumentNotFoundError,
    get_extractor_spec,
    select_input_document,
    select_input_documents,
)
from core.pipeline import run_audit, run_extract, run_pipeline


VALID_MARKDOWN = """
## 1. 赋值理由
社会影响力赋值为2。
规模和服务范围赋值应为1。
提供服务的重要性赋值应为1。
## 2. 采用定级方法及选择权重理由
我们取 α=β=γ=1/3。
经过计算，k=1.4，确定某系统的安全等级为第1级。
"""


class PipelineTests(unittest.TestCase):
    def test_selects_only_markdown_whose_name_contains_required_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            target = unit_dir / "某系统定级报告2025.md"
            target.write_text(VALID_MARKDOWN, encoding="utf-8")
            (unit_dir / "某系统备案表.md").write_text("无关内容", encoding="utf-8")

            selected = select_input_document(get_extractor_spec("DJ-02"), unit_dir)
            self.assertEqual(selected, target)

    def test_extract_accepts_a_system_unit_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory) / "某系统-1"
            unit_dir.mkdir()
            (unit_dir / "某系统定级报告.md").write_text(VALID_MARKDOWN, encoding="utf-8")
            (unit_dir / "其他材料.md").write_text("## 1. 赋值理由\n错误内容", encoding="utf-8")
            output = Path(directory) / "extracted.json"
            result = run_extract(
                item_id="DJ-02",
                input_unit_dir=unit_dir,
                output_path=output,
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), result)
            self.assertEqual(result["社会影响力赋值"], 2)

    def test_missing_required_document_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            (unit_dir / "备案表.md").write_text("无关内容", encoding="utf-8")
            with self.assertRaises(RequiredDocumentNotFoundError):
                run_extract(
                    item_id="DJ-02",
                    input_unit_dir=unit_dir,
                    output_path=unit_dir / "fields.json",
                )

    def test_multiple_required_documents_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            for name in ("系统定级报告.md", "系统定级报告修订版.md"):
                (unit_dir / name).write_text(VALID_MARKDOWN, encoding="utf-8")
            with self.assertRaises(MultipleRequiredDocumentsError):
                run_extract(
                    item_id="DJ-02",
                    input_unit_dir=unit_dir,
                    output_path=unit_dir / "fields.json",
                )

    def test_audit_uses_rule_paired_by_item_id(self) -> None:
        fields = {
            "社会影响力赋值": 2,
            "规模和服务范围赋值": 1,
            "提供服务的重要性赋值": 1,
            "声明安全等级": 1,
            "权重α": 1 / 3,
            "权重β": 1 / 3,
            "权重γ": 1 / 3,
            "声明k值": 1.4,
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "audit.json"
            result = run_audit(
                item_id="dj_02",
                extracted_fields=fields,
                output_path=output,
            )
            self.assertEqual(result["item_id"], "DJ-02")
            self.assertTrue(result["passed"])

    def test_pipeline_reports_selected_source_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory) / "某系统-1"
            unit_dir.mkdir()
            source = unit_dir / "某系统定级报告.md"
            source.write_text(VALID_MARKDOWN, encoding="utf-8")
            result = run_pipeline(
                item_id="DJ-02",
                input_unit_dir=unit_dir,
                extracted_output_path=Path(directory) / "DJ-02.json",
                audit_output_path=Path(directory) / "audit.json",
            )
            self.assertEqual(result["source_document"], str(source))
            self.assertTrue(result["audit"]["passed"])

    def test_selects_two_documents_by_file_or_parent_directory_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory) / "某系统-1"
            inventory_dir = unit_dir / "01_全量资产清单"
            report_dir = unit_dir / "03_定级报告"
            inventory_dir.mkdir(parents=True)
            report_dir.mkdir()
            inventory = inventory_dir / "解析结果.md"
            report = report_dir / "解析结果.md"
            inventory.write_text("资产", encoding="utf-8")
            report.write_text("报告", encoding="utf-8")
            (inventory_dir / "README.md").write_text("说明", encoding="utf-8")
            (report_dir / "README.md").write_text("说明", encoding="utf-8")

            selected = select_input_documents(get_extractor_spec("DJ-09"), unit_dir)

            self.assertEqual(selected, (inventory, report))

    def test_bean_01_selects_kv_json_and_ignores_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            target = unit_dir / "备案信息表.json"
            target.write_text("{}", encoding="utf-8")
            (unit_dir / "备案信息表.md").write_text("表格", encoding="utf-8")

            selected = select_input_documents(get_extractor_spec("BEAN-01"), unit_dir)

            self.assertEqual(selected, (target,))

    def test_bean_02_selects_filing_json_and_asset_inventory_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            filing = unit_dir / "备案信息表.json"
            inventory = unit_dir / "资产清单.md"
            filing.write_text("{}", encoding="utf-8")
            inventory.write_text("| 资产IP |\\n| --- |\\n| 10.0.0.1 |", encoding="utf-8")
            (unit_dir / "备案信息表.md").write_text("旧预览", encoding="utf-8")

            selected = select_input_documents(get_extractor_spec("BEAN-02"), unit_dir)

            self.assertEqual(selected, (filing, inventory))

    def test_zic_01_selects_asset_inventory_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit_dir = Path(directory)
            inventory = unit_dir / "资产清单.md"
            inventory.write_text("| 资产IP |\n| --- |\n| 10.0.0.1 |", encoding="utf-8")

            selected = select_input_documents(get_extractor_spec("ZIC-01"), unit_dir)

            self.assertEqual(selected, (inventory,))


if __name__ == "__main__":
    unittest.main()
