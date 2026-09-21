from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from extractors import dj_01 as dj_01_module
from extractors.dj_01 import (
    DJ01ExtractionError,
    DJ01NotApplicableError,
    extract_dj_01,
    extract_dj_01_data,
)
from extractors.errors import ExtractorNotApplicableError


REPORT = """# 定级报告

## 1. 赋值理由

- 1） **社会影响力赋值**

系统服务于地市及以下各级政府机关，其影响力限于本地范围，社会影响力赋值为2。

- 2） **规模和服务范围赋值**

服务对象付费用户数在100万（含）以下且虚拟主机数在100万（含）以下，所提供服务的规模和服务范围赋值为1。

- 3） **提供服务的重要性赋值**

系统提供的服务为地市及以下各级政府机关，提供服务重要性赋值为2。

## 2. 采用定级方法及选择权重理由

其中I=2（社会影响力赋值），R=1（规模和服务范围赋值），V=2（提供服务的重要性赋值），k=1.74，确定安全等级为第 2 级。
"""


def assessment_markdown(title: str) -> str:
    return f"# {title}\n\n| 检查点 | 答案 |\n| --- | --- |\n| 示例 | 是 |\n"


CLOUD_ASSESSMENT = assessment_markdown("通信网络安全防护符合性评测表－互联网云服务平台")


class DJ01ExtractorTests(unittest.TestCase):
    def test_ignores_empty_html_anchor_in_field_heading(self) -> None:
        report = REPORT.replace(
            "**社会影响力赋值**",
            '<a id="_toc22553"></a>**社会影响力赋值**',
        )

        fields = extract_dj_01_data(report, CLOUD_ASSESSMENT)

        influence = next(
            item for item in fields["赋值理由"] if item["要素"] == "社会影响力"
        )
        self.assertEqual(influence["报告赋值"], 2)
        self.assertIn("社会影响力赋值为2", influence["理由原文"])

    def test_falls_back_to_legacy_service_importance_heading(self) -> None:
        report = REPORT.replace(
            "**提供服务的重要性赋值**",
            "**所提供服务重要性赋值**",
        )

        fields = extract_dj_01_data(report, CLOUD_ASSESSMENT)

        importance = next(
            item
            for item in fields["赋值理由"]
            if item["要素"] == "提供服务的重要性"
        )
        self.assertEqual(importance["报告赋值"], 2)
        self.assertIn("提供服务重要性赋值为2", importance["理由原文"])
        scale = next(
            item
            for item in fields["赋值理由"]
            if item["要素"] == "规模和服务范围"
        )
        self.assertNotIn("所提供服务重要性赋值", scale["理由原文"])
        self.assertNotIn("提供服务重要性赋值为2", scale["理由原文"])

    def test_extracts_reason_paragraphs_values_and_assessment_type(self) -> None:
        result = extract_dj_01_data(REPORT, CLOUD_ASSESSMENT)
        self.assertEqual(
            [item["报告赋值"] for item in result["赋值理由"]], [2, 1, 2]
        )
        self.assertEqual(
            [item["要素"] for item in result["赋值理由"]],
            ["社会影响力", "规模和服务范围", "提供服务的重要性"],
        )
        for item in result["赋值理由"]:
            self.assertIn("赋值", item["理由原文"])
        self.assertEqual(result["评测表网络类型"], "互联网云服务平台")
        self.assertEqual(
            result["评测表标题"], "通信网络安全防护符合性评测表－互联网云服务平台"
        )

    def test_rejects_report_without_assignment_section(self) -> None:
        with self.assertRaises(DJ01NotApplicableError):
            extract_dj_01_data("# 定级报告\n\n没有赋值理由章节。", CLOUD_ASSESSMENT)

    def test_not_applicable_error_is_routed_to_extractor_not_applicable(self) -> None:
        # batch_process / audit_service 通过 except ExtractorNotApplicableError
        # 分支归类“提取器不适用”异常；缺赋值理由章节触发的 DJ-01 异常必须
        # 进入该分支，而不是落入通用 Exception 的“程序错误”状态。
        self.assertTrue(issubclass(DJ01NotApplicableError, ExtractorNotApplicableError))
        self.assertTrue(issubclass(DJ01NotApplicableError, DJ01ExtractionError))
        with self.assertRaises(ExtractorNotApplicableError):
            extract_dj_01_data("# 定级报告\n\n没有赋值理由章节。", CLOUD_ASSESSMENT)

    def test_rejects_assessment_without_title(self) -> None:
        # 复用 FHX-03 逻辑：评测表没有“…评测表－类型”标题即提取异常
        with self.assertRaises(DJ01ExtractionError) as context:
            extract_dj_01_data(REPORT, "# 随便一份文档\n\n没有目标标题。")
        self.assertIn("符合性评测表标题无法提取", str(context.exception))

    def test_wrapper_stages_fields_into_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            parsed.mkdir()
            (parsed / "定级报告.md").write_text(REPORT, encoding="utf-8")
            (parsed / "符合性评测表.md").write_text(CLOUD_ASSESSMENT, encoding="utf-8")

            fields = extract_dj_01(
                (parsed / "定级报告.md", parsed / "符合性评测表.md")
            )

            staged = root / "audits" / "DJ-01" / "DJ-01.json"
            self.assertTrue(staged.is_file())
            payload = json.loads(staged.read_text(encoding="utf-8"))
            self.assertEqual(
                payload["输入文档"],
                ["parsed/定级报告.md", "parsed/符合性评测表.md"],
            )
            self.assertEqual(payload["评测表网络类型"], fields["评测表网络类型"])
            self.assertNotIn("原始输入图片", payload)

    def test_wrapper_rejects_single_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(DJ01ExtractionError):
                extract_dj_01(Path(directory) / "定级报告.md")


class DJ01StandardSectionTests(unittest.TestCase):
    def test_locates_section_from_assessment_title(self) -> None:
        # FHX-03 标题取词 → 类型 → 细化指标章节原文
        result = extract_dj_01_data(REPORT, CLOUD_ASSESSMENT)
        section = result["细化指标章节"]
        self.assertEqual(section["匹配状态"], "评测表标题命中")
        self.assertEqual(section["专用章节号"], "6.17")
        self.assertEqual(section["专用章节标题"], "互联网云服务平台")
        self.assertIn("表47", section["专用章节原文"])
        self.assertIn("付费用户数", section["专用章节原文"])
        self.assertIn("表90", section["通用章节原文"])

    def test_supports_annual_prefix_and_half_width_dash(self) -> None:
        result = extract_dj_01_data(
            REPORT,
            assessment_markdown("2025年度通信网络安全防护符合性评测表-互联网云服务平台"),
        )
        self.assertEqual(result["评测表网络类型"], "互联网云服务平台")
        self.assertEqual(result["细化指标章节"]["专用章节号"], "6.17")

    def test_subtype_keyword_maps_through_taxonomy(self) -> None:
        # 横线后是 A/B 类单元名（如 5G核心网）时，经系统类型划分归到网络类型
        result = extract_dj_01_data(
            REPORT, assessment_markdown("通信网络安全防护符合性评测表－5G核心网")
        )
        self.assertEqual(result["细化指标章节"]["匹配状态"], "评测表标题命中")
        self.assertEqual(result["细化指标章节"]["专用章节号"], "6.3")
        self.assertEqual(result["细化指标章节"]["专用章节标题"], "移动通信网")

    def test_other_keyword_falls_to_generic_section(self) -> None:
        result = extract_dj_01_data(
            REPORT, assessment_markdown("通信网络安全防护符合性评测表－其他")
        )
        section = result["细化指标章节"]
        self.assertEqual(section["匹配状态"], "评测表标题命中")
        self.assertEqual(section["专用章节号"], "6.30")
        self.assertEqual(section["专用章节标题"], "其他业务系统")
        self.assertIn("表90", section["专用章节原文"])

    def test_unknown_keyword_reports_generic_section_only(self) -> None:
        # 类型词在系统类型划分中定位不到：只给通用赋值表并标注未匹配
        result = extract_dj_01_data(
            REPORT, assessment_markdown("通信网络安全防护符合性评测表－魔法平台")
        )
        section = result["细化指标章节"]
        self.assertEqual(section["匹配状态"], "未匹配专用章节")
        self.assertIsNone(section["专用章节原文"])
        self.assertIsNone(section["专用章节号"])
        self.assertIn("魔法平台", section["匹配依据"])
        self.assertIn("网络/系统单元的社会影响力赋值", section["通用章节原文"])

    def test_missing_reference_documents_raise_extraction_error(self) -> None:
        original_taxonomy = dj_01_module._TAXONOMY_PATH
        original_standard = dj_01_module._STANDARD_PATH
        try:
            dj_01_module._TAXONOMY_PATH = Path(tempfile.gettempdir()) / "不存在的类型划分.json"
            dj_01_module._load_type_candidates.cache_clear()
            with self.assertRaises(DJ01ExtractionError):
                extract_dj_01_data(REPORT, CLOUD_ASSESSMENT)

            dj_01_module._TAXONOMY_PATH = original_taxonomy
            dj_01_module._STANDARD_PATH = Path(tempfile.gettempdir()) / "不存在的细化指标.md"
            dj_01_module._load_type_candidates.cache_clear()
            dj_01_module._load_standard_sections.cache_clear()
            with self.assertRaises(DJ01ExtractionError):
                extract_dj_01_data(REPORT, CLOUD_ASSESSMENT)
        finally:
            dj_01_module._TAXONOMY_PATH = original_taxonomy
            dj_01_module._STANDARD_PATH = original_standard
            dj_01_module._load_type_candidates.cache_clear()
            dj_01_module._load_standard_sections.cache_clear()


if __name__ == "__main__":
    unittest.main()
