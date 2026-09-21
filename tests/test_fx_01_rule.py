from __future__ import annotations

import unittest
from audit_rules.fx_01 import audit_fx_01


def fields(problem=True):
    return {
        '风险评估报告文件数': 1,
        '可定位检验结论文件数': 1,
        '包含问题关键词文件数': int(problem),
        '报告检查结果': [{'检验结论范围存在': True, '匹配章节': '检验结论', '问题关键词存在': problem}],
    }


class FX01RuleTests(unittest.TestCase):
    def test_passes_inside_conclusion(self):
        self.assertTrue(audit_fx_01(fields())['passed'])

    def test_no_positive_evidence_fails(self):
        result = audit_fx_01(fields(False))
        self.assertFalse(result['passed'])
        self.assertEqual(result['issues'], ['检验结论栏内未明确识别到风险问题'])

    def test_report_level_evidence_cannot_replace_conclusion(self):
        data = fields()
        data['报告检查结果'][0].update({'检验结论范围存在': False, '报告风险数量证据存在': True})
        with self.assertRaisesRegex(ValueError, '越界'):
            audit_fx_01(data)

    def test_wrong_section_rejected(self):
        data = fields()
        data['报告检查结果'][0]['匹配章节'] = '总体评价'
        with self.assertRaisesRegex(ValueError, '越界'):
            audit_fx_01(data)

    def test_invalid_counts_rejected(self):
        data = fields()
        data['包含问题关键词文件数'] = 2
        with self.assertRaises(ValueError):
            audit_fx_01(data)
