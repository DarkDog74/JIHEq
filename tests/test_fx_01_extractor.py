from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.fx_01 import FX01ExtractionError, FX01SectionNotFoundError, extract_fx_01_documents, extract_fx_01_text


class FX01ExtractorTests(unittest.TestCase):
    def extract(self, text):
        return extract_fx_01_text(text, source_name="风险评估报告.md")

    def test_positive_count_in_value_cell(self):
        result = self.extract('# 检验结论\n| 检验依据 | 存在99个风险 |\n| 检验结论 | 系统存1个脆弱点；低风险1个；很低风险0个。 |\n# 总体评价\n无风险')
        self.assertTrue(result['问题关键词存在'])
        self.assertNotIn('99', result['检验结论原文'])
        self.assertNotIn('无风险', result['检验结论原文'])

    def test_lowest_risk_count_is_positive(self):
        result = self.extract('| **检 验 结 论** | 很高风险0个；低风险0个；很低风险 1 个。 |')
        self.assertTrue(result['问题关键词存在'])

    def test_zero_count_and_overall_risk_cannot_pass(self):
        result = self.extract('# 检验结论\n| 检验依据 | 存在9个风险 |\n| 检验结论 | 存在0个脆弱点，低风险0个。 |\n# 总体评价\n存在1个风险\n# 附录\n存在如下问题：配置错误')
        self.assertTrue(result['检验结论范围存在'])
        self.assertFalse(result['问题关键词存在'])

    def test_no_conclusion_rejects_full_text_evidence(self):
        for text in ['存在5个脆弱点，其中低风险5个。', '# 总体评价\n存在如下问题：配置错误\n# 目录', '[检验结论](#检验结论)\n# 附录\n低风险1个']:
            with self.subTest(text=text):
                self.assertFalse(self.extract(text)['检验结论范围存在'])
                self.assertFalse(self.extract(text)['问题关键词存在'])

    def test_plain_text_heading_and_problem_list(self):
        result = self.extract('检 验 结 论\n存在如下安全问题：应急预案内容不完善。\n目 录\n附录存在9个风险')
        self.assertTrue(result['问题关键词存在'])
        self.assertNotIn('附录', result['检验结论原文'])

    def test_heading_stops_at_next_heading(self):
        for boundary in ['# 总体评价', '## 其他章节', '目 录']:
            self.assertFalse(self.extract('# 检验结论\n未发现风险\n'+boundary+'\n存在1个风险')['问题关键词存在'])

    def test_empty_or_negative_list_does_not_pass(self):
        for body in ['', '存在如下问题：', '存在如下问题：无。', '未发现风险，等级为低。', '存在0个脆弱点', '不存在1个风险', '未发现1个脆弱点', '不存在如下问题：配置错误']:
            self.assertFalse(self.extract('# 检验结论\n'+body)['问题关键词存在'])

    def test_other_cells_and_following_rows_do_not_leak(self):
        result = self.extract('| 检验结论 | 无风险 | 其他字段 | 低风险1个 |\n| 附录 | 存在9个风险 |')
        self.assertFalse(result['问题关键词存在'])

    def test_flattened_table_is_not_a_conclusion_value(self):
        result = self.extract('# 检验结论\n| 系统名称 检验依据 检验结论 | 系统名 检验依据存在9个风险 |\n# 总体评价')
        self.assertFalse(result['检验结论范围存在'])

    def test_multiple_value_cells_are_ambiguous(self):
        with self.assertRaises(FX01ExtractionError):
            self.extract('| 检验结论 | 无风险 |\n| 检验结论 | 低风险1个 |')

    def test_flattened_table_recovers_only_conclusion(self):
        text = '|||第1页 检验结论||||\n| 系统名称 检验依据 检验结论 批准：某人 | 系统A 检验依据：低风险99个 本次检验共发现系统存在1个脆弱点，低风险1个。审核：某人 | 检验结束 | 存在88个风险 |\n# 附录\n存在77个风险'
        result = self.extract(text)
        self.assertTrue(result['问题关键词存在'])
        self.assertEqual(result['检验结论原文'], '本次检验共发现系统存在1个脆弱点，低风险1个。')
        self.assertEqual(result['检验结论起始行'], 2)

    def test_flattened_zero_risk_does_not_use_other_evidence(self):
        result = self.extract('| 系统名称 检验依据 检验结论 | 系统A 检验依据低风险9个 本次检验存在0个脆弱点，低风险0个。签发日期：低风险8个 |\n# 总体评价\n低风险7个')
        self.assertTrue(result['检验结论范围存在'])
        self.assertFalse(result['问题关键词存在'])

    def test_flattened_conclusion_accepts_cell_end_and_whitespace(self):
        result = self.extract('| 系 统 名 称 检 验 依 据 检 验 结 论 | 系统A<br>本 次 检 验共发现1个脆弱点，很低风险1个。 |')
        self.assertTrue(result['问题关键词存在'])
        self.assertTrue(result['检验结论原文'].startswith('本 次 检 验'))

    def test_flattened_conclusion_stops_at_each_signature_field(self):
        for field in ['签发日期', '批准：', '审核:', '编 写：']:
            with self.subTest(field=field):
                result = self.extract('| 系统名称 检验依据 检验结论 | 系统A 本次检验未发现风险。'+field+' 低风险9个 |')
                self.assertFalse(result['问题关键词存在'])
                self.assertEqual(result['检验结论原文'], '本次检验未发现风险。')

    def test_flattened_multiple_starts_are_rejected(self):
        with self.assertRaisesRegex(FX01ExtractionError, '多个'):
            self.extract('| 系统名称 检验依据 检验结论 | 本次检验无风险；本次检验低风险1个 |')

    def test_flattened_multiple_rows_are_rejected(self):
        with self.assertRaisesRegex(FX01ExtractionError, '多个'):
            self.extract('| 系统名称 检验依据 检验结论 | 本次检验无风险 |\n| 系统名称 检验依据 检验结论 | 本次检验低风险1个 |')

    def test_flattened_signature_before_conclusion_is_rejected(self):
        with self.assertRaisesRegex(FX01ExtractionError, '边界不明确'):
            self.extract('| 系统名称 检验依据 检验结论 | 系统A 签发日期：本次检验低风险1个 |')

    def test_flattened_unknown_field_after_conclusion_is_rejected(self):
        with self.assertRaisesRegex(FX01ExtractionError, '最后一个业务字段'):
            self.extract('| 系统名称 检验依据 检验结论 附录风险 | 系统A 本次检验无风险 附录低风险5个 |')

    def test_flattened_anchor_outside_value_cell_is_not_used(self):
        result = self.extract('| 系统名称 检验依据 检验结论 | 系统A 无结论 | 其他字段 | 本次检验低风险1个 |')
        self.assertFalse(result['检验结论范围存在'])

    def test_sentence_without_table_labels_is_not_used(self):
        result = self.extract('| 参考资料中提及检验结论 | 本次检验低风险1个 |')
        self.assertFalse(result['检验结论范围存在'])

    def test_standard_cell_takes_precedence_over_flattened_candidate(self):
        result = self.extract('| 检验结论 | 无风险 |\n| 系统名称 检验依据 检验结论 | 本次检验低风险1个 |')
        self.assertFalse(result['问题关键词存在'])

    def test_secondary_report_cannot_supply_conclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory)/'风险评估报告.md'
            primary.write_text('全文存在9个风险', encoding='utf-8')
            (primary.parent/'风险评估报告（2）.md').write_text('| 检验结论 | 存在1个脆弱点 |', encoding='utf-8')
            with self.assertRaises(FX01SectionNotFoundError):
                extract_fx_01_documents(primary)

    def test_documents_without_conclusion_raise_missing_error(self):
        with tempfile.TemporaryDirectory() as directory:
            primary = Path(directory)/'风险评估报告.md'
            primary.write_text('# 总体评价\n存在1个风险', encoding='utf-8')
            with self.assertRaisesRegex(FX01SectionNotFoundError, '检验结论'):
                extract_fx_01_documents(primary)
