from __future__ import annotations

import unittest

from parser.emergency_evidence import (
    extract_drill_date_evidence,
    extract_revision_date_evidence,
)


class EmergencyEvidenceTests(unittest.TestCase):
    def test_revision_dates_only_come_from_revision_column(self) -> None:
        markdown = """# 预案管理名录

| 版本 | 修订记录 | 审核日期 |
|---|---|---|
| V1.0 | 2023年5月12日 | 2025年1月1日 |
| V2.0 | 2024年3月1日 | 2025年2月1日 |

# 演练记录

2026年1月1日
"""
        records = extract_revision_date_evidence(markdown)
        self.assertEqual([item.start_date.isoformat() for item in records], ["2023-05-12", "2024-03-01"])
        self.assertTrue(all(item.field == "修订记录" for item in records))

    def test_revision_table_requires_controlled_scope(self) -> None:
        markdown = """# 其他记录

| 版本 | 更新日期 |
|---|---|
| V1.0 | 2024年3月1日 |
"""
        self.assertEqual(extract_revision_date_evidence(markdown), [])

    def test_month_precision_keeps_full_possible_range(self) -> None:
        markdown = """# 修订记录

| 版本 | 修订记录 |
|---|---|
| V1.0 | 2024年7月 |
"""
        record = extract_revision_date_evidence(markdown)[0]
        self.assertEqual(record.precision, "month")
        self.assertEqual(record.start_date.isoformat(), "2024-07-01")
        self.assertEqual(record.end_date.isoformat(), "2024-07-31")

    def test_drill_date_comes_from_time_people_section(self) -> None:
        markdown = """# 二、演练过程

2025年12月31日完成整改。

# 三、演练时间、地点、人员

（一）时间：2024年3月1日 9:00

# 四、其他
"""
        record = extract_drill_date_evidence(markdown)
        self.assertIsNotNone(record)
        self.assertEqual(record.start_date.isoformat(), "2024-03-01")

    def test_schedule_rows_do_not_override_direct_drill_time(self) -> None:
        markdown = """# 演练时间段安排

2025年1月1日

# 演练时间：2024年7月17日
"""
        record = extract_drill_date_evidence(markdown)
        self.assertIsNotNone(record)
        self.assertEqual(record.start_date.isoformat(), "2024-07-17")


if __name__ == "__main__":
    unittest.main()
