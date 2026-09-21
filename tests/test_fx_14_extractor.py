from __future__ import annotations

import unittest

from extractors.fx_14 import extract_fx_14_text


class FX14ExtractorTests(unittest.TestCase):
    def test_extracts_preventive_measure_from_target_type_column(self) -> None:
        result = extract_fx_14_text(
            """正文中的预防性安全措施不算证据。
# 附录C 已有安全措施分析表
| 已有安全措施 | 类型 |
| --- | --- |
| 访问控制 | 预防性安全措施 |
| 数据备份 | 保护性安全措施 |
# 附录D 下一表
"""
        )
        self.assertTrue(result["类型列存在"])
        self.assertEqual(
            result["预防性安全措施证据"][0]["关键词"], "预防性安全措施"
        )

    def test_does_not_accept_protective_measure_for_fx_14(self) -> None:
        result = extract_fx_14_text(
            """# 附录C 已有安全措施分析表
| 已有安全措施 | 类型 |
| --- | --- |
| 数据备份 | 保护性安全措施 |
# 附录D 下一表
"""
        )
        self.assertEqual(result["预防性安全措施证据"], [])


if __name__ == "__main__":
    unittest.main()
