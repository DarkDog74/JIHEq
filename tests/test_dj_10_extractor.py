from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.batch_process import run_batch
from core.model_client import ModelReply
from extractors.dj_10 import (
    extract_dj_10,
    extract_dj_10_data,
)
from extractors.registry import get_extractor_spec
from extractors.errors import TargetSectionNotFoundError


REPORT = r"""# 1、 系统基本情况

## 1. 系统概述

系统为中国移动的客户提供云业务服务，部署了防火墙等安全设备。

- 1\) **业务/应用范围**

提供云业务。

- 2\) **服务范围**

全省客户。

## 2. 管理情况

公司建立了安全组织架构。
"""


class DJ10ExtractorTests(unittest.TestCase):
    def test_extracts_only_overview_until_next_section(self) -> None:
        result = extract_dj_10_data(REPORT)
        self.assertIn("提供云业务服务", result["系统概述"])
        self.assertIn("业务/应用范围", result["系统概述"])
        self.assertNotIn("管理情况", result["系统概述"])
        self.assertNotIn("安全组织架构", result["系统概述"])
        self.assertEqual(set(result), {"系统概述"})

    def test_rejects_report_without_overview(self) -> None:
        with self.assertRaises(TargetSectionNotFoundError):
            extract_dj_10_data("# 定级报告\n\n直接进入管理情况。")

    def test_registered_input_is_only_the_level_report(self) -> None:
        spec = get_extractor_spec("DJ-10")
        self.assertEqual(spec.document_requirements, (("定级报告",),))
        self.assertEqual(spec.document_requirement_file_suffixes, ((".md",),))

    def test_wrapper_stages_only_report_overview_into_audits(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            parsed = root / "parsed"
            parsed.mkdir()
            (parsed / "定级报告.md").write_text(REPORT, encoding="utf-8")

            extract_dj_10(parsed / "定级报告.md")

            staged = root / "audits" / "DJ-10" / "DJ-10.json"
            self.assertTrue(staged.is_file())
            payload = json.loads(staged.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"系统概述", "输入文档"})
            self.assertEqual(payload["输入文档"], ["parsed/定级报告.md"])

    def test_batch_new_output_does_not_require_overwrite_or_filing_form(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            dataset.mkdir()
            (dataset / "定级报告.md").write_text(REPORT, encoding="utf-8")
            reply = ModelReply(
                text=json.dumps(
                    {
                        "是否属于": True,
                        "匹配层级": "网络类型",
                        "网络类型": "互联网云服务平台",
                        "A类单元": None,
                        "B类单元": None,
                        "判断依据": "提供云业务",
                    },
                    ensure_ascii=False,
                ),
                model="fake",
                elapsed_ms=1,
            )

            with patch("audit_rules.dj_10._chat", return_value=reply):
                summary = run_batch(
                    dataset_dir=dataset,
                    item_id="DJ-10",
                    output_dir=root / "output",
                    unit_depth=0,
                )

            self.assertEqual(summary["classification"], "passed")
            self.assertEqual(summary["runs"][0]["reason_code"], "passed")
            self.assertEqual(
                summary["document_requirements"], [["定级报告"]]
            )


if __name__ == "__main__":
    unittest.main()
