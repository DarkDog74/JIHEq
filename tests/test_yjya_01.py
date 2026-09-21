from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from audit_rules.yjya_01 import audit_yjya_01
from extractors.errors import TargetSectionNotFoundError
from extractors.yjya_01 import extract_yjya_01, extract_yjya_01_data


def plan(subject: str) -> str:
    return f"""# 预案管理名录
| 序号 | 演练记录 |
|---|---|
| 1 | 2025年1月1日<br>开展演练：{subject} |
"""


def report(subject: str, title: str = "演练科目内容") -> str:
    return f"# 六、{title}\n{subject}\n# 七、其他\n"


class YJYA01Tests(unittest.TestCase):
    def test_same_topics_in_different_order_pass(self) -> None:
        fields = extract_yjya_01_data(plan("网页篡改、DDOS攻击"), report("ddos攻击，网页篡改"))
        self.assertTrue(audit_yjya_01(fields)["passed"])

    def test_generic_attack_suffix_is_not_removed(self) -> None:
        fields = extract_yjya_01_data(plan("网页篡改"), report("网页篡改攻击"))
        result = audit_yjya_01(fields)
        self.assertFalse(result["passed"])
        self.assertEqual(result["plan_only_topics"], ["网页篡改"])
        self.assertEqual(result["report_only_topics"], ["网页篡改攻击"])

    def test_missing_plan_management_record_is_missing_target(self) -> None:
        with self.assertRaises(TargetSectionNotFoundError):
            extract_yjya_01_data("# 普通应急预案", report("网页篡改"))

    def test_manifest_selects_current_files_not_previous_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "应急预案.md").write_text(plan("当前科目"), encoding="utf-8")
            (root / "上一份应急预案.md").write_text(plan("旧科目"), encoding="utf-8")
            (root / "应急演练报告.md").write_text(report("当前科目"), encoding="utf-8")
            (root / "上一份应急演练报告.md").write_text(report("旧科目"), encoding="utf-8")
            manifest = root / "应急材料分析.json"
            manifest.write_text(
                json.dumps(
                    {
                        "files": [
                            {"用途": "应急预案", "解析状态": "success", "标准Markdown": "应急预案.md"},
                            {"用途": "应急预案", "解析状态": "success", "标准Markdown": "上一份应急预案.md"},
                            {"用途": "应急演练报告", "解析状态": "success", "标准Markdown": "应急演练报告.md"},
                            {"用途": "应急演练报告", "解析状态": "success", "标准Markdown": "上一份应急演练报告.md"},
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            fields = extract_yjya_01(manifest)
            self.assertEqual(fields["预案演练科目"], ["当前科目"])
            self.assertEqual(fields["报告演练科目"], ["当前科目"])


if __name__ == "__main__":
    unittest.main()
