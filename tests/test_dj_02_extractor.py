from __future__ import annotations

import unittest

from extractors.dj_02 import (
    DJ02ExtractionError,
    DJ02NotApplicableError,
    DJ02CalculationExtractionError,
    extract_dj_02_calculation_text,
    extract_dj_02_text,
)


VALID_MARKDOWN = """
- [1. 赋值理由](#目录链接)

## 1. 赋值理由

### 社会影响力
社会影响力赋值为 2。

### 规模和服务范围
规模和服务范围赋值应为1。

### 提供服务的重要性
提供服务的重要性赋值应为一。

## 2. 采用定级方法及选择权重理由
社会影响力赋值为5。
我们取 α=β=γ=1/3。
经过计算，k = 1.4，确定某系统的安全等级为第1级。
"""


VALUE_FIELDS = (
    "社会影响力赋值",
    "规模和服务范围赋值",
    "提供服务的重要性赋值",
)


def values_only(result: dict[str, object]) -> dict[str, object]:
    return {field: result[field] for field in VALUE_FIELDS}


class DJ02ExtractorTests(unittest.TestCase):
    def test_extracts_chain_weights_and_declared_k(self) -> None:
        result = extract_dj_02_calculation_text(VALID_MARKDOWN)
        for symbol in "αβγ":
            self.assertEqual(result[f"权重{symbol}"], 1 / 3)
        self.assertEqual(result["声明k值"], 1.4)

    def test_explicit_non_equal_weights_override_equal_prose(self) -> None:
        markdown = VALID_MARKDOWN.replace("α=β=γ=1/3", "权重均等，因此令α=0.5、β=0.3、γ=0.2")
        result = extract_dj_02_calculation_text(markdown)
        self.assertEqual([result[f"权重{s}"] for s in "αβγ"], [0.5, 0.3, 0.2])

    def test_supports_weights_list_in_prose(self) -> None:
        markdown = VALID_MARKDOWN.replace("我们取 α=β=γ=1/3。", "α、β、γ分别表示社会影响力、规模和服务范围、所提供服务的重要性权重，分别为1/3、1/3、1/3。")
        result = extract_dj_02_calculation_text(markdown)
        self.assertEqual([result[f"权重{s}"] for s in "αβγ"], [1 / 3] * 3)

    def test_supports_formatting_fullwidth_and_fraction(self) -> None:
        markdown = VALID_MARKDOWN.replace("α=β=γ=1/3", "**α＝１／２**，β = .3，γ＝0.2").replace("k = 1.4", "*Ｋ＝１．４０*")
        result = extract_dj_02_calculation_text(markdown)
        self.assertEqual([result[f"权重{s}"] for s in "αβγ"], [0.5, 0.3, 0.2])
        self.assertEqual(result["声明k值"], 1.4)

    def test_does_not_invent_weights_or_k_from_equal_prose_or_range(self) -> None:
        markdown = VALID_MARKDOWN.replace("α=β=γ=1/3", "三要素权重均等").replace("k = 1.4", "1 ≤ k < 1.5")
        result = extract_dj_02_calculation_text(markdown)
        self.assertIsNone(result["权重α"])
        self.assertIsNone(result["声明k值"])

    def test_ignores_other_sections_and_image_metadata(self) -> None:
        markdown = VALID_MARKDOWN.replace("我们取 α=β=γ=1/3。", "")
        markdown = markdown.replace("k = 1.4", "1 ≤ k < 1.5")
        markdown += '\n[[IMAGE id=IMG-001 alt="α=β=γ=1/3,k=1.4"]] ![k=1.4](image.png)\n<!-- k=1.4 -->\n'
        markdown += "## 3. 安全保护等级\nα=β=γ=1/3，k=1.4。"
        result = extract_dj_02_calculation_text(markdown)
        self.assertIsNone(result["声明k值"])
        self.assertIsNone(result["权重α"])

    def test_does_not_use_formula_as_numeric_k(self) -> None:
        for expression in ("k=Round1{Log2[α×2^I+β×2^R+γ×2^V]}", "k=1+0.4", "k=1/3", "1<=k<=1.5", "k=1.4×0", "k=1.4÷2", "k=1.4*0"):
            with self.subTest(expression=expression):
                result = extract_dj_02_calculation_text(VALID_MARKDOWN.replace("k = 1.4", expression))
                self.assertIsNone(result["声明k值"])

    def test_does_not_read_k_after_plain_numbered_next_heading(self) -> None:
        for heading in ("3. 安全保护等级", "- 3） **某系统安全保护等级**"):
            markdown = VALID_MARKDOWN.replace("k = 1.4", "1≤k<1.5")
            result = extract_dj_02_calculation_text(markdown + f"\n{heading}\n复核另项数值k=1.4。")
            self.assertIsNone(result["声明k值"])

    def test_keeps_declared_k_precision_for_rule_to_check(self) -> None:
        result = extract_dj_02_calculation_text(VALID_MARKDOWN.replace("k = 1.4", "k=1.415037"))
        self.assertEqual(result["声明k值"], 1.415037)

    def test_preserves_invalid_weight_values_for_rule_validation(self) -> None:
        result = extract_dj_02_calculation_text(VALID_MARKDOWN.replace("α=β=γ=1/3", "α=-0.1、β=1.1、γ=0"))
        self.assertEqual([result[f"权重{s}"] for s in "αβγ"], [-0.1, 1.1, 0])

    def test_conflicting_calculation_values_are_extraction_errors(self) -> None:
        for extra in ("α=0.5。", "k=2。", "α=1/0。"):
            with self.subTest(extra=extra):
                with self.assertRaises(DJ02CalculationExtractionError):
                    extract_dj_02_calculation_text(VALID_MARKDOWN + extra)

    def test_equivalent_repeated_values_are_not_conflicts(self) -> None:
        result = extract_dj_02_calculation_text(VALID_MARKDOWN + "α=1/3。k=1.40。")
        self.assertEqual(result["声明k值"], 1.4)

    def test_legacy_interface_remains_independent_of_calculation_errors(self) -> None:
        result = extract_dj_02_text(VALID_MARKDOWN + "α=1/0。k=9。")
        self.assertEqual(set(result), set(VALUE_FIELDS) | {"声明安全等级"})

    def test_ignores_empty_html_anchor_in_field_heading(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "### 社会影响力",
            '### <a id="_toc22553"></a>社会影响力赋值',
        )

        self.assertEqual(extract_dj_02_text(markdown)["社会影响力赋值"], 2)

    def test_supports_legacy_service_importance_heading_without_parent_section(self) -> None:
        markdown = """
# 某系统安全等级的确定
- 1） **社会影响力赋值**
赋值应为2。
- 2） **规模和服务范围赋值**
赋值应为3。
- 3） **所提供服务重要性赋值**
赋值应为1。
## 2. 采用定级方法及选择权重理由
确定某系统的安全等级为第2级。
"""

        self.assertEqual(
            values_only(extract_dj_02_text(markdown)),
            {
                "社会影响力赋值": 2,
                "规模和服务范围赋值": 3,
                "提供服务的重要性赋值": 1,
            },
        )

    def test_extracts_values_only_from_target_section(self) -> None:
        self.assertEqual(
            values_only(extract_dj_02_text(VALID_MARKDOWN)),
            {
                "社会影响力赋值": 2,
                "规模和服务范围赋值": 1,
                "提供服务的重要性赋值": 1,
            },
        )
        result = extract_dj_02_text(VALID_MARKDOWN)
        self.assertEqual(result["声明安全等级"], 1)

    def test_supports_bold_heading_and_full_width_values(self) -> None:
        markdown = """
**## 1．赋值理由**
社会影响力赋值为２。
规模和服务范围赋值应为１。
提供服务的重要性赋值应为１。
## 2. 采用定级方法及选择权重理由
"""
        self.assertEqual(extract_dj_02_text(markdown)["社会影响力赋值"], 2)

    def test_missing_target_section_is_not_applicable(self) -> None:
        with self.assertRaises(DJ02NotApplicableError):
            extract_dj_02_text("## 其他章节\n无关内容")

    def test_supports_known_template_with_all_three_exact_field_headings(self) -> None:
        markdown = r"""
# 2、 某系统安全等级的确定
- 1\) **社会影响力赋值**
系统社会影响力赋值应为2。
- 2\) **规模和服务范围赋值**
系统规模和服务范围赋值应为3。
- 3\) **提供服务的重要性赋值**
系统所提供服务的重要性赋值应为2。
## 1. 采用定级方法及选择权重理由
I=2，R=3，V=2。
"""
        self.assertEqual(
            values_only(extract_dj_02_text(markdown)),
            {
                "社会影响力赋值": 2,
                "规模和服务范围赋值": 3,
                "提供服务的重要性赋值": 2,
            },
        )

    def test_uses_field_subsection_when_body_wording_omits_de(self) -> None:
        markdown = """
## 1. 赋值理由
- 1） **社会影响力赋值**
社会影响力赋值为1。
- 2） **规模和服务范围赋值**
规模和服务范围赋值为1。
- 3） **提供服务的重要性赋值**
提供服务重要性赋值为2。
## 2. 采用定级方法及选择权重理由
"""
        self.assertEqual(extract_dj_02_text(markdown)["提供服务的重要性赋值"], 2)

    def test_supports_bold_numbered_headings_and_assignment_variants(self) -> None:
        markdown = """
## **1.** 赋值理由
### **1)** 社会影响力赋值
社会影响力赋值2。
### **2)** 规模和服务范围赋值
规模和服务范围赋值取最高值为4。
### **3)** 提供服务的重要性赋值
所提供服务的重要性赋值应为3。
## **2.** 采用定级方法及选择权重理由
"""
        self.assertEqual(
            values_only(extract_dj_02_text(markdown)),
            {
                "社会影响力赋值": 2,
                "规模和服务范围赋值": 4,
                "提供服务的重要性赋值": 3,
            },
        )

    def test_supports_level_three_assignment_section(self) -> None:
        markdown = """
### 1.赋值理由
- 1） **社会影响力赋值**
社会影响力赋值4。
- 2） **规模和服务范围赋值**
规模和服务范围的赋值为4。
- 3） **提供服务的重要性赋值**
提供服务的重要性赋值为3。
### 2.采用定级方法及选择权重理由
"""
        self.assertEqual(extract_dj_02_text(markdown)["社会影响力赋值"], 4)

    def test_missing_field_raises_extraction_error(self) -> None:
        markdown = """
## 1. 赋值理由
社会影响力赋值为2。
规模和服务范围赋值应为1。
## 2. 采用定级方法及选择权重理由
"""
        with self.assertRaisesRegex(DJ02ExtractionError, "提供服务的重要性赋值"):
            extract_dj_02_text(markdown)

    def test_uses_declared_level_from_method_section_only(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "确定某系统的安全等级为第1级。",
            "确定某系统的安全等级为第1级。\n## 3. 安全保护等级\n安全等级确定为2级",
        )
        self.assertEqual(extract_dj_02_text(markdown)["声明安全等级"], 1)

    def test_supports_security_level_calculation_method_heading(self) -> None:
        markdown = """
## 1. 赋值理由
社会影响力赋值为1。
规模和服务范围赋值为1。
提供服务的重要性赋值为1。
# 2、 安 全 等 级 的 计 算 方 法
经过计算，k = 1，确定某系统的安全等级为第 1 级。
## 3、安全保护等级
安全等级为第2级。
"""
        self.assertEqual(extract_dj_02_text(markdown)["声明安全等级"], 1)

    def test_method_heading_requires_a_complete_title_match(self) -> None:
        markdown = """
## 1. 赋值理由
社会影响力赋值为1。
规模和服务范围赋值为1。
提供服务的重要性赋值为1。
# 2、关于采用定级方法的说明
确定某系统的安全等级为第5级。
# 2、安全等级的计算方法
确定某系统的安全等级为第1级。
"""
        self.assertEqual(extract_dj_02_text(markdown)["声明安全等级"], 1)

    def test_conflicting_values_raise_extraction_error(self) -> None:
        markdown = VALID_MARKDOWN.replace(
            "社会影响力赋值为 2。", "社会影响力赋值为 2，又说明社会影响力赋值为3。"
        )
        with self.assertRaisesRegex(DJ02ExtractionError, "冲突值"):
            extract_dj_02_text(markdown)


if __name__ == "__main__":
    unittest.main()
