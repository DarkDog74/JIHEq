from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from extractors.ylp_01 import (
    YLP01ExtractionError,
    YLP01SectionNotFoundError,
    extract_ylp_01,
    extract_ylp_01_text,
)


def _plan_table(rows: str) -> str:
    return f"""# 基础安全演练计划

| 序号 | 归属单位 | 参演系统 | 恶意程序类 |  | 网络攻击类 |  | 安全隐患类 |  | 计划演练时间 | 备注 |
|---|---|---|---|---|---|---|---|---|---|---|
|  |  |  | 恶意程序感染 | 勒索软件 | 拒绝服务攻击 | 网页篡改 | 弱口令 | 配置错误 |  |  |
{rows}

# 接报演练计划

| 参演系统 | 网络攻击类 | 计划演练时间 |
|---|---|---|
| 不应读取的系统 | √ | 2026年8月 |
"""


class YLP01ExtractorTests(unittest.TestCase):
    def test_extracts_two_level_event_table_and_only_nonempty_system_rows(self) -> None:
        markdown = _plan_table(
            "\n".join(
                [
                    "| 1 | 单位甲 | 系统甲 | √ |  |  |  |  |  | 2026年7月 |  |",
                    "| 2 | 单位乙 | 系统乙 |  |  |  | √ |  | √ | 2026年8月 | 已确认 |",
                    "| 3 | 单位丙 |  | √ |  |  |  |  |  | 2026年9月 | 预留行 |",
                    "| 4 | 单位丁 | 系统丁 |  |  |  |  |  |  | √ | √ |",
                ]
            )
        )

        fields = extract_ylp_01_text(markdown)

        self.assertEqual(fields["目标工作表"], "基础安全演练计划")
        self.assertEqual(fields["事件类别"], ["恶意程序类", "网络攻击类", "安全隐患类"])
        records = fields["演练计划记录"]
        self.assertEqual([record["参演系统"] for record in records], ["系统甲", "系统乙", "系统丁"])
        self.assertEqual([record["勾选数量"] for record in records], [1, 2, 0])
        self.assertEqual(records[0]["已勾选事件"], ["恶意程序类/恶意程序感染"])
        self.assertEqual(
            records[1]["已勾选事件"],
            ["网络攻击类/网页篡改", "安全隐患类/配置错误"],
        )
        self.assertEqual(records[2]["已勾选事件"], [])

    def test_checkmarks_in_time_and_remark_or_later_section_do_not_count(self) -> None:
        markdown = _plan_table(
            "| 1 | 单位甲 | 目标系统 |  |  |  |  |  |  | √ | √ |"
        ).replace(
            "| 不应读取的系统 | √ | 2026年8月 |",
            "| 目标系统 | √ | 2026年8月 |",
        )

        fields = extract_ylp_01_text(markdown)

        self.assertEqual(len(fields["演练计划记录"]), 1)
        self.assertEqual(fields["演练计划记录"][0]["参演系统"], "目标系统")
        self.assertEqual(fields["演练计划记录"][0]["勾选数量"], 0)
        self.assertEqual(fields["演练计划记录"][0]["勾选单元格"], [])

    def test_only_the_required_sqrt_character_counts_as_a_checkmark(self) -> None:
        markdown = _plan_table(
            "| 1 | 单位甲 | 目标系统 | ✓ | ✔ |  |  |  |  | 2026年7月 |  |"
        )

        fields = extract_ylp_01_text(markdown)

        self.assertEqual(fields["演练计划记录"][0]["勾选数量"], 0)

    def test_file_entry_point_reads_utf8_markdown(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "2026年演练计划.md"
            path.write_text(
                _plan_table("| 1 | 单位甲 | 系统甲 |  | √ |  |  |  |  | 2026年7月 |  |"),
                encoding="utf-8-sig",
            )

            fields = extract_ylp_01(path)

        self.assertEqual(fields["演练计划记录"][0]["参演系统"], "系统甲")
        self.assertEqual(fields["演练计划记录"][0]["勾选数量"], 1)

    def test_missing_target_section_is_distinct_from_structure_error(self) -> None:
        markdown = """# 接报演练计划

| 参演系统 | 恶意程序类 | 网络攻击类 | 安全隐患类 | 计划演练时间 |
|---|---|---|---|---|
| 系统甲 | √ |  |  | 2026年7月 |
"""

        with self.assertRaisesRegex(
            YLP01SectionNotFoundError, "不存在.*基础安全演练计划"
        ):
            extract_ylp_01_text(markdown)

    def test_target_section_without_required_two_level_header_is_structure_error(self) -> None:
        markdown = """# 基础安全演练计划

| 序号 | 参演系统 | 演练事件 | 计划演练时间 |
|---|---|---|---|
| 1 | 系统甲 | 勒索攻击 | 2026年7月 |
"""

        with self.assertRaisesRegex(YLP01ExtractionError, "未找到.*三类事件"):
            extract_ylp_01_text(markdown)

    def test_target_table_without_nonempty_system_row_is_structure_error(self) -> None:
        markdown = _plan_table(
            "| 1 | 单位甲 |  | √ |  |  |  |  |  | 2026年7月 | 预留行 |"
        )

        with self.assertRaisesRegex(YLP01ExtractionError, "没有.*有效计划行"):
            extract_ylp_01_text(markdown)


if __name__ == "__main__":
    unittest.main()
