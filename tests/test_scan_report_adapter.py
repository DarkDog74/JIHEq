from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from parser.scan_report_adapter import (
    build_scan_report_summary,
    is_scan_archive_candidate,
    parse_scan_archive,
)


def write_zip(path: Path, files: dict[str, bytes | str]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(
                name,
                content.encode("utf-8") if isinstance(content, str) else content,
            )


def static_html(task_name: str, task_type: str, template: str = "") -> str:
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>报表</title></head>
<body><table><tr><th>任务名称</th><td>{task_name}</td></tr>
<tr><th>任务类型</th><td>{task_type}</td></tr>
<tr><th>漏洞扫描模板</th><td>{template}</td></tr></table></body></html>"""


class ScanReportAdapterTests(unittest.TestCase):
    def test_old_table_template_classifies_password_and_vulnerability(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            baseline = root / "baseline.zip"
            vulnerability = root / "vulnerability.zip"
            write_zip(baseline, {"index.html": static_html("系统-弱口令", "口令猜测")})
            write_zip(
                vulnerability,
                {"index.html": static_html("系统-漏洞", "评估任务", "常态化扫描模板")},
            )

            summary = build_scan_report_summary(
                [(baseline, "baseline.zip"), (vulnerability, "vulnerability.zip")]
            )

            self.assertEqual(summary["识别材料类型"], ["基线", "漏扫"])
            self.assertEqual(summary["可读取HTML压缩包数"], 2)

    def test_new_window_data_template_is_parsed_without_executing_javascript(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "new.zip"
            data = {
                "categories": [
                    {
                        "data": {
                            "taskName": "新系统-弱口令",
                            "taskTypeName": "口令猜测",
                            "vulnTemplate": "",
                        }
                    }
                ]
            }
            html = (
                "<html><head><meta charset=UTF-8><title>网络安全漏洞扫描系统安全评估报告</title>"
                "<script>window.data = "
                + json.dumps(data, ensure_ascii=False)
                + ";</script></head></html>"
            )
            write_zip(path, {"index.html": html})

            result = parse_scan_archive(path)

            self.assertEqual(result["状态"], "已识别")
            self.assertEqual(result["识别材料类型"], ["基线"])
            self.assertEqual(result["标识字段"]["任务类型"], "口令猜测")

    def test_penetration_in_report_identity_is_recognized(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "penetration.zip"
            write_zip(path, {"index.html": static_html("系统渗透测试报告", "渗透测试")})

            result = parse_scan_archive(path)

            self.assertEqual(result["识别材料类型"], ["渗透测试"])

    def test_boilerplate_keywords_outside_identity_fields_do_not_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unknown.zip"
            html = static_html("普通任务", "其他任务") + "<p>建议修复弱口令和漏洞。</p>"
            write_zip(path, {"index.html": html})

            result = parse_scan_archive(path)

            self.assertEqual(result["状态"], "类型未识别")
            self.assertEqual(result["识别材料类型"], [])

    def test_baseline_category_word_alone_is_not_a_configured_trigger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "baseline-word.zip"
            write_zip(path, {"index.html": static_html("系统基线检查", "其他任务")})

            result = parse_scan_archive(path)

            self.assertEqual(result["状态"], "类型未识别")
            self.assertEqual(result["识别材料类型"], [])

    def test_gb18030_html_and_large_member_inventory_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "many.zip"
            html = static_html("系统-漏洞", "评估任务").replace(
                'charset="utf-8"', 'charset="gb18030"'
            )
            files: dict[str, bytes | str] = {"index.html": html.encode("gb18030")}
            files.update({f"host/{index}.html": "<p>主机</p>" for index in range(1000)})
            write_zip(path, files)

            result = parse_scan_archive(path)

            self.assertEqual(result["HTML文件数"], 1001)
            self.assertEqual(result["识别材料类型"], ["漏扫"])

    def test_no_html_corrupt_and_oversized_archives_have_explicit_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            no_html = root / "扫描附件.zip"
            corrupt = root / "漏扫损坏.zip"
            oversized = root / "oversized.zip"
            write_zip(no_html, {"report.docx": b"word"})
            corrupt.write_bytes(b"not-a-zip")
            write_zip(oversized, {"index.html": "0123456789"})

            self.assertTrue(is_scan_archive_candidate(no_html))
            self.assertEqual(parse_scan_archive(no_html)["状态"], "ZIP内无HTML")
            self.assertEqual(parse_scan_archive(corrupt)["状态"], "ZIP损坏")
            with patch("parser.scan_report_adapter.MAX_ENTRY_SIZE", 4):
                self.assertEqual(parse_scan_archive(oversized)["状态"], "入口HTML过大")


if __name__ == "__main__":
    unittest.main()
