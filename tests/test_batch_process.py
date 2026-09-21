from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from core.batch_process import discover_unit_directories, format_batch_summary, run_batch


VALID_MARKDOWN = """
## 1. 赋值理由
社会影响力赋值为2。
规模和服务范围赋值应为1。
提供服务的重要性赋值应为1。
## 2. 采用定级方法及选择权重理由
我们取 α=β=γ=1/3。
经过计算，k=1.4，确定某系统的安全等级为第1级。
"""

DJ04_MARKDOWN = """
|  | 修订历史 (REVISION HISTORY) |  |  |  |  |
|  | 版本号 | 日 期 | 作 者 | 审阅人 | 摘 要 |
| 1.0 |  | 2025-12-19 |  | 张三 |  |
"""

DJ03_MARKDOWN = """<!-- 页眉：某 系统 定级报告 -->
某系统定级报告
| 文档名称 | 某系统定级报告 |
"""

DJ05_MARKDOWN = """某系统定级报告
**Ver.1.5**
**2025年5月**
| **修订历史** (REVISION HISTORY) |  |  |  |  |
| **版本号** | **日 期** | **作 者** | **审阅人** | **摘 要** |
| 1.5 | 2025-5-31 | 张三 |  |  |
"""

DJ09_INVENTORY = """| 资产IP | 映射IP | 资产类型 | 资产型号 | 设备厂商 | 域名 | 网络位置 | 定级对象名称 | 责任人 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 10.0.0.1 |  | 服务器 | M1 | 厂商A |  | 内网 | 系统A | 张三 |
"""

DJ09_REPORT = """| 资产IP | 资产类型 | 资产型号 | 设备厂商 | 网络位置 | 责任人 |
| --- | --- | --- | --- | --- | --- |
| 10.0.0.1 | 服务器 | M1 | 厂商A | 内网 | 张三 |
"""

DJ07_MARKDOWN = r"""
- 2\) **网络拓扑结构**
[[IMAGE id=IMG-002 file="topology.png"]]
- 3\) **硬件设备部署情况**
- 4\) **网络边界划分**
已进行网络边界划分。
"""

BEAN01_PAYLOAD = {
    "系统名称*": "系统A",
    "填表人*": "",
}

BEAN02_PAYLOAD = {
    "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
        {"对应IP段*": "10.0.0.1-2"}
    ],
}

BEAN02_INVENTORY = """| 资产IP | 映射IP | 类型 | 网络位置（内网/外网） |
| --- | --- | --- | --- |
| 10.0.0.1/32 | 192.168.0.1 | 服务器 | 外网 |
| 10.0.0.2/32 | 192.168.0.2 | 服务器 | 外网 |
| 10.0.0.9 | 203.0.113.9 | 服务器 | 内网 |
"""

ZIC01_INVENTORY = """| 资产IP | 设备厂商 |
| --- | --- |
| 10.0.0.1 | XX |
"""

ZIC02_INVENTORY = """| 资产IP | 资产类型 |
| --- | --- |
| 10.0.0.1 | 服务器 |
| 10.0.0.2 | 虚拟机 |
| 10.0.0.3 | 虚拟机 |
"""

FX08_MARKDOWN = """# A.3 系统组件和单元资产赋值表
| 序号 | 资产名称 | 识别内容 |  |  | 系统组件和单元资产赋值 |  |  |  |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
|  |  | 分类 | 分类详情 | 关键服务 | 保密性 | 完整性 | 可用性 | 综合赋值Vc |
| C1 | 服务器 | 系统单元 | 服务器 | 关键服务 | 3 | 3 | 4 | 3 |
# A.4 人力资源赋值表
"""

FX09_MARKDOWN = """# 附录B 威胁识别及赋值表
| 序号 | 资产名称 | 威胁来源 | 威胁种类 | 威胁行为 |
| --- | --- | --- | --- | --- |
| 1 | 系统A | 人为 | 功能损害 | 网络攻击 |
# 附录C 已有安全措施分析表
"""

FX15_MARKDOWN = """## 6.1 风险计算方法
风险计算采用相乘法。
## 6.2 风险评价方法
"""

FX10_MARKDOWN = """# 附录B 威胁识别及赋值表
| 序号 | 资产名称 | 威胁来源 | 威胁种类 | 威胁行为 | 威胁行为能力赋值 | 威胁频率赋值 | 威胁时机赋值 | 威胁结果 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | 系统A | 人为 | 功能损害 | 网络攻击 | 2 | 2 | 0 | 2 |
# 附录C 已有安全措施分析表
"""

FX13_PASS_MARKDOWN = """# 附录C 已有安全措施分析表
| 各安全层面 | 已有安全措施 | 类型 | 功能描述 |
| --- | --- | --- | --- |
| 技术层面 | 访问控制 | 保护性安全措施 | 网络防护 |
# 附录D 脆弱性识别及赋值表
"""

FX13_FAIL_MARKDOWN = """# 附录C 已有安全措施分析表
| 各安全层面 | 已有安全措施 | 类型 | 功能描述 |
| --- | --- | --- | --- |
| 技术层面 | 数据备份 | 预防性安全措施 | 数据恢复 |
# 附录D 脆弱性识别及赋值表
"""

ZIC03_INCOMPLETE_INVENTORY = """| 资产IP | 映射IP | 资产类型 | 责任人 |
| --- | --- | --- | --- |
| 10.0.0.1 |  | 服务器 | 张三 |
| 10.0.0.2 |  | 服务器 |  |
"""

ZIC03_COMPLETE_INVENTORY = """| 资产IP | 资产类型 | 责任人 |
| --- | --- | --- |
| 10.0.0.1 | 服务器 | 张三 |
"""

LEVEL_TWO_REPORT = """## 2. 安全等级的计算方法
经过计算，确定某系统的安全等级为第2级。
## 3. 安全保护等级
后续内容。
"""

FILE_ANALYSIS_WITHOUT_RISK_REPORT = {
    "files": [{"source": "系统定级报告.docx", "category_name": "定级报告"}]
}

FX02_LEVEL_THREE_REPORT = """## 2. 采用定级方法及选择权重理由
经过计算，确定某系统的安全等级为第3级。
## 3. 安全保护等级
后续内容。
"""


def fx02_risk_report(end_date: str) -> str:
    return f"""# 检验结论
| 检验类别 | 委托检验 | 检验开始 | 2024.1.1 |
| --- | --- | --- | --- |
| 检验来源 | 委托方提供 | 检验结束 | {end_date} |
# 总体评价
"""

FHX05_WITH_READABLE_RISK_REPORT = {
    "files": [
        {
            "source": "系统风险评估报告.docx",
            "category_name": "风险评估报告",
            "size_bytes": 1024,
            "status": "reserved",
            "error": None,
        }
    ]
}

FHX05_WITH_UNREADABLE_RISK_REPORT = {
    "files": [
        {
            "source": "损坏的风险评估报告.docx",
            "category_name": "风险评估报告",
            "size_bytes": 0,
            "status": "failed",
            "error": "无法打开",
        }
    ]
}

FHX03_VALID_MARKDOWN = """|  |  |  |
| --- | --- | --- |
| 通信网络安全防护符合性评测表－融合网络 |  |  |
"""

FHX03_INVALID_MARKDOWN = """|  |  |
| --- | --- |
| 通信网络安全防护符合性评测表－某完整系统名称 |  |
"""

FX01_WITH_PROBLEM = """# 检验结论
经检测，当前系统主要存在如下问题：
1. 应急预案内容不完善。
依据GB/T 20984-2022进行综合评价。
# 目录
"""

FX01_WITHOUT_PROBLEM = """# 检验结论
本次评估未发现中高危风险，安全防护较好。
依据GB/T 20984-2022进行综合评价。
# 目录
"""

FX12_COMPLETE = """# 5.2 脆弱性识别方法
3）漏洞扫描
使用漏洞检测工具。
6）渗透测试
对指定目标进行测试。
"""

FX05_RISK_REPORT = """### 1.4.3. 系统组件和单元资产
| 序号 | 设备名称 | 数量 |
| --- | --- | --- |
| 1 | 服务器 | 2 |
## 1.5. 下一节
"""

FX05_ASSET_INVENTORY = """| 资产IP | 映射IP | 资产类型 |
| --- | --- | --- |
| 10.0.0.1 | 192.168.1.1 | 服务器 |
| 10.0.0.2 | 192.168.1.2 | 服务器 |
"""

FX04_WITH_TOPOLOGY_IMAGE = """# 项目范围
- 拓扑图

[[IMAGE id=IMG-001 file="topology.png"]]
"""

FHX02_INVALID_MARKDOWN = """| 序号 | 检查点 | 答案 | 答案说明 |
| --- | --- | --- | --- |
| 01 | 是否符合要求A？ | 是 |  |
| 02 | 是否符合要求B？ | 请选择... |  |
| 03 | 是否符合要求C？ |  |  |
"""

FHX02_VALID_MARKDOWN = """| 序号 | 检查点 | 答案 |
| --- | --- | --- |
| 01 | 是否符合要求A？ | 是 |
| 02 | 是否符合要求B？ | 不适用 |
"""


def make_unit(root: Path, department: str, system: str) -> Path:
    unit = root / department / system
    unit.mkdir(parents=True)
    return unit


class BatchProcessTests(unittest.TestCase):
    def test_fx_04_reports_each_unit_and_missing_image_reason(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed_unit = make_unit(root, "部门A", "系统1")
            failed_unit = make_unit(root, "部门A", "系统2")
            (passed_unit / "风险评估报告.md").write_text(
                FX04_WITH_TOPOLOGY_IMAGE, encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                "- 拓扑图\n本系统组网如下。", encoding="utf-8"
            )

            output = Path(directory) / "output"
            summary = run_batch(
                dataset_dir=root, item_id="FX-04", output_dir=output
            )
            terminal_output = format_batch_summary(summary, output)

            self.assertEqual(
                summary["audit_counts"],
                {"passed": 1, "failed": 1, "not_applicable": 0},
            )
            self.assertIn("[FX-04] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("[FX-04] ❌ 不通过 | 部门A/系统2", terminal_output)
            self.assertIn("关联IMAGE=0", terminal_output)
            self.assertIn("没有IMAGE标识", terminal_output)

    def test_discovers_department_system_units_at_depth_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = make_unit(root, "部门A", "系统1")
            second = make_unit(root, "部门B", "系统2")
            self.assertEqual(discover_unit_directories(root), [first, second])

    def test_depth_zero_treats_dataset_directory_as_single_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            unit = Path(directory)
            (unit / "附件").mkdir()
            self.assertEqual(discover_unit_directories(unit, unit_depth=0), [unit])

    def test_each_unit_selects_only_its_grading_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            valid_unit = make_unit(root, "部门A", "系统1")
            missing_unit = make_unit(root, "部门A", "系统2")
            (valid_unit / "系统1定级报告.md").write_text(VALID_MARKDOWN, encoding="utf-8")
            (valid_unit / "系统1备案表.md").write_text("无关内容", encoding="utf-8")
            (missing_unit / "系统2备案表.md").write_text("无关内容", encoding="utf-8")

            output = Path(directory) / "output"
            summary = run_batch(dataset_dir=root, item_id="DJ-02", output_dir=output)

            self.assertEqual(summary["total_units"], 2)
            self.assertEqual(summary["document_name_contains"], ["定级报告"])
            self.assertEqual(summary["status_counts"]["success"], 1)
            self.assertEqual(summary["status_counts"]["missing_document"], 1)
            fields_path = output / "部门A" / "系统1" / "DJ-02" / "DJ-02.json"
            fields = json.loads(fields_path.read_text(encoding="utf-8"))
            self.assertEqual(fields["社会影响力赋值"], 2)
            self.assertFalse(summary["batch_passed"])
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-02] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("计算等级=第1级", terminal_output)
            self.assertIn("缺少目标文档：1", terminal_output)
            self.assertIn(
                "[DJ-02] ❌ 不通过 | 部门A/系统2 | 原因：缺少目标文档：定级报告",
                terminal_output,
            )

    def test_dj_02_rejects_wrong_k_even_when_level_matches(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "定级报告.md").write_text(
                VALID_MARKDOWN.replace("k=1.4", "k=1"), encoding="utf-8"
            )
            output = Path(directory) / "output"
            summary = run_batch(dataset_dir=root, item_id="DJ-02", output_dir=output)
            run = summary["runs"][0]
            self.assertEqual(run["classification"], "failed")
            self.assertEqual(run["status"], "success")
            self.assertEqual(run["declared_k"], 1)
            self.assertEqual(run["calculated_k"], 1.4)
            self.assertFalse(run["k_matches"])
            self.assertTrue(run["level_matches"])
            self.assertIn("声明k=1", format_batch_summary(summary, output))
            saved = json.loads((output / "部门A/系统1/DJ-02/稽核结果.json").read_text())
            self.assertFalse(saved["passed"])

    def test_dj_02_missing_k_and_conflicting_weights_have_distinct_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            for name, markdown in (
                ("缺k", VALID_MARKDOWN.replace("k=1.4", "1≤k<1.5")),
                ("权重冲突", VALID_MARKDOWN + "α=0.5。"),
            ):
                unit = make_unit(root, "部门A", name)
                (unit / "定级报告.md").write_text(markdown, encoding="utf-8")
            summary = run_batch(dataset_dir=root, item_id="DJ-02", output_dir=Path(directory) / "output")
            runs = {r["unit"].split("/")[-1]: r for r in summary["runs"]}
            self.assertEqual(runs["缺k"]["status"], "success")
            self.assertEqual(runs["权重冲突"]["status"], "extraction_error")
            self.assertTrue(all(r["classification"] == "failed" for r in runs.values()))

    def test_units_are_audited_independently_without_cross_unit_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            first = make_unit(root, "部门A", "系统1")
            second = make_unit(root, "部门A", "系统2")
            (first / "系统1定级报告.md").write_text(VALID_MARKDOWN, encoding="utf-8")
            (second / "系统2定级报告.md").write_text(
                VALID_MARKDOWN.replace("社会影响力赋值为2", "社会影响力赋值为3")
                .replace("k=1.4", "k=2.0")
                .replace("第1级", "第2级"),
                encoding="utf-8",
            )

            summary = run_batch(
                dataset_dir=root,
                item_id="DJ-02",
                output_dir=Path(directory) / "output",
            )
            self.assertEqual(summary["status_counts"]["success"], 2)
            self.assertEqual(summary["audit_counts"]["passed"], 2)
            self.assertTrue(summary["batch_passed"])

    def test_terminal_shows_reason_for_audit_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(
                VALID_MARKDOWN.replace("第1级", "第2级"), encoding="utf-8"
            )
            output = Path(directory) / "output"
            summary = run_batch(dataset_dir=root, item_id="DJ-02", output_dir=output)
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-02] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("原因：公式计算结果为第1级，报告声明为第2级", terminal_output)

    def test_duplicate_matching_documents_are_not_chosen_arbitrarily(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            for name in ("系统定级报告.md", "系统定级报告修订版.md"):
                (unit / name).write_text(VALID_MARKDOWN, encoding="utf-8")

            summary = run_batch(
                dataset_dir=root,
                item_id="DJ-02",
                output_dir=Path(directory) / "output",
            )
            self.assertEqual(summary["status_counts"]["multiple_documents"], 1)
            self.assertFalse(summary["batch_passed"])

    def test_malformed_target_document_is_recorded_as_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统定级报告.md").write_text(
                "## 1. 赋值理由\n社会影响力赋值为2。\n## 2. 下一节",
                encoding="utf-8",
            )
            summary = run_batch(
                dataset_dir=root,
                item_id="DJ-02",
                output_dir=Path(directory) / "output",
            )
            self.assertEqual(summary["status_counts"]["execution_error"], 1)
            self.assertFalse(summary["batch_passed"])
            terminal_output = format_batch_summary(summary, Path(directory) / "output")
            self.assertIn("未找到完整的定级方法", terminal_output)
            self.assertIn("（DJ02ExtractionError）", terminal_output)

    def test_dj_04_uses_generic_output_name_and_terminal_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(DJ04_MARKDOWN, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-04", output_dir=output)

            fields_path = output / "部门A" / "系统1" / "DJ-04" / "DJ-04.json"
            self.assertTrue(fields_path.is_file())
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-04] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("修订历史行数=1", terminal_output)
            self.assertIn("原因：修订历史第1行缺少作者", terminal_output)
            self.assertNotIn("k=None", terminal_output)

    def test_dj_04_reports_missing_revision_history_separately(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "核心网部", "省内信令网-3")
            (unit / "省内信令网定级报告.md").write_text(
                "# 省内信令网定级报告\n\n正文中不包含修订历史表。",
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-04", output_dir=output)

            self.assertEqual(summary["status_counts"]["missing_revision_history"], 1)
            terminal_output = format_batch_summary(summary, output)
            self.assertIn(
                "[DJ-04] ❌ 不通过 | 核心网部/省内信令网-3 | 原因：不存在修订历史",
                terminal_output,
            )
            self.assertIn("不存在修订历史：1", terminal_output)
            self.assertIn("提取失败：0", terminal_output)

    def test_dj_03_compares_header_and_document_name_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(DJ03_MARKDOWN, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-03", output_dir=output)

            fields_path = output / "部门A" / "系统1" / "DJ-03" / "DJ-03.json"
            self.assertTrue(fields_path.is_file())
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-03] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("页眉与文档名称一致", terminal_output)
            self.assertNotIn("k=None", terminal_output)

    def test_dj_03_reports_literal_empty_header_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(
                "<!-- 页眉：空 -->\n| 文档名称 | 某系统定级报告 |",
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-03", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn(
                "[DJ-03] ❌ 不通过 | 部门A/系统1 | 原因：缺少页眉",
                terminal_output,
            )
            self.assertNotIn("页眉与文档名称不一致", terminal_output)

    def test_dj_05_compares_cover_with_latest_revision_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(DJ05_MARKDOWN, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-05", output_dir=output)

            fields_path = output / "部门A" / "系统1" / "DJ-05" / "DJ-05.json"
            self.assertTrue(fields_path.is_file())
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-05] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("封面版本1.5、日期2025-05与最新修订记录一致", terminal_output)
            self.assertNotIn("k=None", terminal_output)

    def test_dj_09_uses_asset_inventory_and_grading_report_together(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            inventory_dir = unit / "01_全量资产清单"
            report_dir = unit / "03_定级报告"
            inventory_dir.mkdir()
            report_dir.mkdir()
            (inventory_dir / "解析结果.md").write_text(
                DJ09_INVENTORY, encoding="utf-8"
            )
            (report_dir / "解析结果.md").write_text(
                DJ09_REPORT, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-09", output_dir=output)

            self.assertTrue(summary["batch_passed"])
            self.assertEqual(
                summary["document_requirements"], [["资产清单"], ["定级报告"]]
            )
            fields_path = output / "部门A" / "系统1" / "DJ-09" / "DJ-09.json"
            self.assertTrue(fields_path.is_file())
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-09] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("资产一致", terminal_output)

    def test_dj_07_checks_topology_image_and_network_boundary_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "系统1定级报告.md").write_text(
                DJ07_MARKDOWN, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="DJ-07", output_dir=output)

            fields_path = output / "部门A" / "系统1" / "DJ-07" / "DJ-07.json"
            self.assertTrue(fields_path.is_file())
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[DJ-07] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("拓扑标题章节内存在1个IMAGE标识", terminal_output)

    def test_bean_01_reports_missing_required_fields_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "备案信息表.json").write_text(
                json.dumps(BEAN01_PAYLOAD, ensure_ascii=False), encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="BEAN-01", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[BEAN-01] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("必填项=2 | 缺失=1", terminal_output)
            self.assertIn("填表人：未填写", terminal_output)

    def test_bean_02_compares_filing_ranges_with_asset_inventory_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "备案信息表.json").write_text(
                json.dumps(BEAN02_PAYLOAD, ensure_ascii=False), encoding="utf-8"
            )
            (unit / "资产清单.md").write_text(BEAN02_INVENTORY, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="BEAN-02", output_dir=output)

            self.assertTrue(summary["batch_passed"])
            self.assertEqual(
                summary["document_requirements"], [["备案信息表"], ["资产清单"]]
            )
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[BEAN-02] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("备案IP=2个 | 资产清单外网资产IP=2个", terminal_output)

    def test_bean_02_fails_in_batch_when_filing_ip_is_empty_but_inventory_has_ip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "备案信息表.json").write_text(
                json.dumps(
                    {
                        "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
                            {"对应IP段*": ""}
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (unit / "资产清单.md").write_text(BEAN02_INVENTORY, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="BEAN-02", output_dir=output)

            self.assertFalse(summary["batch_passed"])
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[BEAN-02] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("备案IP=0个 | 资产清单外网资产IP=2个", terminal_output)
            self.assertIn("资产清单外网资产IP多出2个 IP", terminal_output)

    def test_bean_02_passes_in_batch_when_both_sides_have_no_ip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "备案信息表.json").write_text(
                json.dumps(
                    {
                        "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
                            {"对应IP段*": ""}
                        ]
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (unit / "资产清单.md").write_text(
                "| 资产IP | 资产类型 | 网络位置 |\n"
                "| --- | --- | --- |\n"
                "| 10.0.0.1 | 服务器 | 内网 |\n",
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="BEAN-02", output_dir=output)

            self.assertTrue(summary["batch_passed"])
            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[BEAN-02] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn("备案IP=0个 | 资产清单外网资产IP=0个", terminal_output)

    def test_zic_01_reports_placeholder_location_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "资产清单.md").write_text(ZIC01_INVENTORY, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="ZIC-01", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[ZIC-01] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("资产清单行数=1 | 模糊占位符=1", terminal_output)
            self.assertIn("资产清单第1行“设备厂商”包含XX", terminal_output)

    def test_zic_02_summarizes_invalid_type_in_terminal_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "资产清单.md").write_text(ZIC02_INVENTORY, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="ZIC-02", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("资产类型行数=3 | 非标准类型=2", terminal_output)
            self.assertIn("非标准类型：虚拟机×2", terminal_output)
            self.assertNotIn("资产清单第2行", terminal_output)

    def test_fx_08_checks_asset_assignment_table_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            unit = make_unit(root, "部门A", "系统1")
            (unit / "风险评估报告.md").write_text(FX08_MARKDOWN, encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-08", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[FX-08] ✅ 通过 | 部门A/系统1", terminal_output)
            self.assertIn(
                "保密性列=是 | 完整性列=是 | 可用性列=是 | "
                "赋值行=1 | 完整=1 | 问题行=0",
                terminal_output,
            )

    def test_fx_09_checks_required_columns_in_batch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed = make_unit(root, "部门A", "完整表头")
            failed = make_unit(root, "部门A", "缺少列")
            (passed / "风险评估报告.md").write_text(FX09_MARKDOWN, encoding="utf-8")
            (failed / "风险评估报告.md").write_text(
                FX09_MARKDOWN.replace(" | 威胁行为 |", " | 威胁行为能力赋值 |"),
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-09", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-09] ✅ 通过 | 部门A/完整表头 | 威胁来源=是 | 威胁种类=是 | 威胁行为=是",
                terminal_output,
            )
            self.assertIn(
                "[FX-09] ❌ 不通过 | 部门A/缺少列 | 威胁来源=是 | 威胁种类=是 | 威胁行为=否",
                terminal_output,
            )
            self.assertIn("威胁识别及赋值表缺少列：威胁行为", terminal_output)

    def test_fx_15_outputs_matched_method_for_each_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed = make_unit(root, "部门A", "使用相乘法")
            failed = make_unit(root, "部门A", "未写方法")
            (passed / "风险评估报告.md").write_text(FX15_MARKDOWN, encoding="utf-8")
            (failed / "风险评估报告.md").write_text(
                FX15_MARKDOWN.replace("采用相乘法", "采用定量方式"),
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-15", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-15] ✅ 通过 | 部门A/使用相乘法 | 风险计算方法=相乘法",
                terminal_output,
            )
            self.assertIn(
                "[FX-15] ❌ 不通过 | 部门A/未写方法 | 风险计算方法=未命中",
                terminal_output,
            )
            self.assertIn("风险计算方法章节未包含矩阵法或相乘法", terminal_output)

    def test_fx_10_checks_every_assignment_row_and_classifies_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed = make_unit(root, "部门A", "完整赋值")
            failed = make_unit(root, "部门A", "赋值缺失")
            unstructured = make_unit(root, "部门A", "表格压平")
            make_unit(root, "部门A", "缺少风险报告")
            (passed / "风险评估报告.md").write_text(FX10_MARKDOWN, encoding="utf-8")
            (failed / "风险评估报告.md").write_text(
                FX10_MARKDOWN.replace("| 2 | 2 | 0 | 2 |", "| 待赋值 | 空 | 无 | 缺失 |"),
                encoding="utf-8",
            )
            (unstructured / "风险评估报告.md").write_text(
                """##### 附录 B 威胁识别及赋值表
序号 威胁来源 威胁种类 威胁行为 威胁行为能力赋值 威胁频率赋值 威胁时机赋值 威胁结果
1 人为 功能损害 网络攻击 2 2 0 2
##### 附录 C 已有安全措施分析表
""",
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-10", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-10] ✅ 通过 | 部门A/完整赋值 | 赋值行=1 | 完整=1 | 问题行=0 | 非数值=0",
                terminal_output,
            )
            self.assertIn(
                "[FX-10] ❌ 不通过 | 部门A/赋值缺失 | 赋值行=1 | 完整=0 | 问题行=1 | 非数值=0",
                terminal_output,
            )
            self.assertIn(
                "问题字段：未找到赋值数字×1",
                terminal_output,
            )
            self.assertIn(
                "[FX-10] ✅ 通过 | 部门A/表格压平 | 赋值行=1 | 完整=1 | 问题行=0 | 非数值=0",
                terminal_output,
            )
            self.assertIn(
                "[FX-10] ❌ 不通过 | 部门A/缺少风险报告 | 原因：缺少目标文档：风险评估报告.md",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "完整赋值" / "FX-10" / "FX-10.json").is_file()
            )

    def test_zic_03_reports_pass_fail_and_missing_document_per_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            failed_unit = make_unit(root, "部门A", "系统1")
            passed_unit = make_unit(root, "部门A", "系统2")
            make_unit(root, "部门A", "系统3")
            (failed_unit / "资产清单.md").write_text(
                ZIC03_INCOMPLETE_INVENTORY, encoding="utf-8"
            )
            (passed_unit / "全量资产清单.md").write_text(
                ZIC03_COMPLETE_INVENTORY, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="ZIC-03", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[ZIC-03] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("资产记录=2 | 不完整记录=2 | 缺失单元格=3", terminal_output)
            self.assertIn("缺失字段：映射IP×2、责任人×1", terminal_output)
            self.assertIn("[ZIC-03] ✅ 通过 | 部门A/系统2", terminal_output)
            self.assertIn("[ZIC-03] ❌ 不通过 | 部门A/系统3 | 原因：缺少目标文档：资产清单", terminal_output)
            self.assertTrue((output / "部门A" / "系统1" / "ZIC-03" / "ZIC-03.json").is_file())

    def test_fx_02_handles_interval_not_applicable_and_conditional_missing_document(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            level_one = make_unit(root, "部门A", "一级系统")
            level_two = make_unit(root, "部门A", "二级系统")
            level_three = make_unit(root, "部门A", "三级超期")
            missing_previous = make_unit(root, "部门A", "三级缺上一份")

            (level_one / "定级报告.md").write_text(VALID_MARKDOWN, encoding="utf-8")
            (level_two / "定级报告.md").write_text(
                LEVEL_TWO_REPORT, encoding="utf-8"
            )
            (level_two / "风险评估报告.md").write_text(
                fx02_risk_report("2026-05-20"), encoding="utf-8"
            )
            (level_two / "上一份风险评估报告.md").write_text(
                fx02_risk_report("2024-05-20"), encoding="utf-8"
            )
            for unit in (level_three, missing_previous):
                (unit / "定级报告.md").write_text(
                    FX02_LEVEL_THREE_REPORT, encoding="utf-8"
                )
                (unit / "风险评估报告.md").write_text(
                    fx02_risk_report("2025-01-02"), encoding="utf-8"
                )
            (level_three / "上一份风险评估报告.md").write_text(
                fx02_risk_report("2024-01-01"), encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-02", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[FX-02] ⏭️ 不适用 | 部门A/一级系统 | 声明等级=第1级 | 本项不适用", terminal_output)
            self.assertIn("[FX-02] ✅ 通过 | 部门A/二级系统", terminal_output)
            self.assertIn("间隔=730天 | 上限=2年", terminal_output)
            self.assertIn("[FX-02] ❌ 不通过 | 部门A/三级超期", terminal_output)
            self.assertIn("超过1年", terminal_output)
            self.assertIn("[FX-02] ❌ 不通过 | 部门A/三级缺上一份 | 原因：缺少目标文档：上一份风险评估报告.md", terminal_output)

    def test_fhx_05_checks_only_level_one_and_requires_readable_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            missing_unit = make_unit(root, "部门A", "一级缺报告")
            readable_unit = make_unit(root, "部门A", "一级报告可读")
            unreadable_unit = make_unit(root, "部门A", "一级报告不可读")
            level_two_unit = make_unit(root, "部门A", "二级系统")
            make_unit(root, "部门A", "缺定级报告")

            cases = (
                (missing_unit, VALID_MARKDOWN, FILE_ANALYSIS_WITHOUT_RISK_REPORT),
                (readable_unit, VALID_MARKDOWN, FHX05_WITH_READABLE_RISK_REPORT),
                (unreadable_unit, VALID_MARKDOWN, FHX05_WITH_UNREADABLE_RISK_REPORT),
                (level_two_unit, LEVEL_TWO_REPORT, FILE_ANALYSIS_WITHOUT_RISK_REPORT),
            )
            for unit, report_markdown, analysis in cases:
                (unit / "定级报告.md").write_text(report_markdown, encoding="utf-8")
                (unit / "文件分析.json").write_text(
                    json.dumps(analysis, ensure_ascii=False), encoding="utf-8"
                )
            (root / "部门A" / "缺定级报告" / "文件分析.json").write_text(
                json.dumps(FHX05_WITH_READABLE_RISK_REPORT, ensure_ascii=False),
                encoding="utf-8",
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FHX-05", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FHX-05] ❌ 不通过 | 部门A/一级缺报告 | 声明等级=第1级 | 风险评估报告记录=0份 | 可读取=0份",
                terminal_output,
            )
            self.assertIn("一级系统缺少风险评估报告", terminal_output)
            self.assertIn(
                "[FHX-05] ✅ 通过 | 部门A/一级报告可读 | 声明等级=第1级 | 风险评估报告记录=1份 | 可读取=1份",
                terminal_output,
            )
            self.assertIn(
                "[FHX-05] ❌ 不通过 | 部门A/一级报告不可读 | 声明等级=第1级 | 风险评估报告记录=1份 | 可读取=0份",
                terminal_output,
            )
            self.assertIn("一级系统没有可读取的风险评估报告", terminal_output)
            self.assertIn(
                "[FHX-05] ⏭️ 不适用 | 部门A/二级系统 | 声明等级=第2级",
                terminal_output,
            )
            self.assertIn(
                "[FHX-05] ❌ 不通过 | 部门A/缺定级报告 | 原因：缺少目标文档：定级报告",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "一级报告可读" / "FHX-05" / "FHX-05.json").is_file()
            )

    def test_fx_01_checks_problem_only_inside_overall_section(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed_unit = make_unit(root, "部门A", "包含问题")
            failed_unit = make_unit(root, "部门A", "没有问题")
            invalid_unit = make_unit(root, "部门A", "缺少总体评价")
            make_unit(root, "部门A", "缺少风险报告")

            (passed_unit / "风险评估报告.md").write_text(
                FX01_WITH_PROBLEM, encoding="utf-8"
            )
            (passed_unit / "风险评估报告（2）.md").write_text(
                "| 只有图表 |", encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                FX01_WITHOUT_PROBLEM + "\n# 项目概述\n系统存在如下问题：越界文本。",
                encoding="utf-8",
            )
            (invalid_unit / "风险评估报告.md").write_text(
                "# 目录\n未解析出总体评价。", encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-01", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-01] ✅ 通过 | 部门A/包含问题 | 风险报告=1份 | 有效评价/结论=1份 | 包含问题=1份",
                terminal_output,
            )
            self.assertIn(
                "[FX-01] ❌ 不通过 | 部门A/没有问题 | 风险报告=1份 | 有效评价/结论=1份 | 包含问题=0份",
                terminal_output,
            )
            self.assertIn("检验结论栏内未明确识别到风险问题", terminal_output)
            self.assertIn(
                "[FX-01] ❌ 不通过 | 部门A/缺少总体评价 | 原因：风险评估报告均未找到",
                terminal_output,
            )
            self.assertIn(
                "[FX-01] ❌ 不通过 | 部门A/缺少风险报告 | 原因：缺少目标文档：风险评估报告.md",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "包含问题" / "FX-01" / "FX-01.json").is_file()
            )

    def test_fx_05_compares_asset_counts_per_unit_and_classifies_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed_unit = make_unit(root, "部门A", "全部对应")
            failed_unit = make_unit(root, "部门A", "两边不一致")
            variant_unit = make_unit(root, "部门A", "标题带识别后缀")
            missing_unit = make_unit(root, "部门A", "缺少风险报告")

            for unit in (passed_unit, failed_unit, variant_unit):
                (unit / "资产清单.md").write_text(FX05_ASSET_INVENTORY, encoding="utf-8")
            (passed_unit / "风险评估报告.md").write_text(
                FX05_RISK_REPORT, encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                FX05_RISK_REPORT.replace("| 1 | 服务器 | 2 |", "| 1 | 服务器 | 3 |"),
                encoding="utf-8",
            )
            (variant_unit / "风险评估报告.md").write_text(
                FX05_RISK_REPORT.replace("系统组件和单元资产", "系统组件和单元资产识别"),
                encoding="utf-8",
            )
            (missing_unit / "资产清单.md").write_text(
                FX05_ASSET_INVENTORY, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-05", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-05] ✅ 通过 | 部门A/全部对应 | 风险报告数量=2 | 资产清单记录数=2",
                terminal_output,
            )
            self.assertIn("[FX-05] ❌ 不通过 | 部门A/两边不一致", terminal_output)
            self.assertIn("数量不一致（相差1）", terminal_output)
            self.assertIn(
                "[FX-05] ✅ 通过 | 部门A/标题带识别后缀 | 风险报告数量=2 | 资产清单记录数=2",
                terminal_output,
            )
            self.assertIn(
                "[FX-05] ❌ 不通过 | 部门A/缺少风险报告 | 原因：缺少目标文档：风险评估报告.md",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "全部对应" / "FX-05" / "FX-05.json").is_file()
            )

    def test_fx_12_requires_three_scan_material_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed_unit = make_unit(root, "部门A", "三类齐全")
            partial_unit = make_unit(root, "部门A", "仅基线")
            failed_unit = make_unit(root, "部门A", "HTML类型未识别")
            make_unit(root, "部门A", "缺扫描报告")

            def write_scan_summary(unit: Path, matched: list[str]) -> None:
                types = ("基线", "漏扫", "渗透测试")
                evidence = [{"材料类型": t, "字段": "任务类型", "命中内容": t, "源文件": t + ".zip"} for t in matched]
                (unit / "扫描报告.json").write_text(
                    json.dumps(
                        {
                            "扫描报告压缩包数": max(1, len(matched)),
                            "可读取HTML压缩包数": max(1, len(matched)),
                            "类型存在性": {item: item in matched for item in types},
                            "识别材料类型": matched,
                            "类型证据": evidence,
                            "异常压缩包": (
                                []
                                if matched
                                else [{"异常原因": "HTML标识字段未命中材料类型"}]
                            ),
                            "压缩包检查结果": [{"源文件": t + ".zip"} for t in matched] or [{"源文件": "扫描.zip"}],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            write_scan_summary(passed_unit, ["基线", "漏扫", "渗透测试"])
            write_scan_summary(partial_unit, ["基线"])
            write_scan_summary(failed_unit, [])
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-12", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-12] ✅ 通过 | 部门A/三类齐全 | ZIP=3 | 基线=是 | 漏扫=是 | 渗透测试=是",
                terminal_output,
            )
            self.assertIn(
                "[FX-12] ❌ 不通过 | 部门A/HTML类型未识别 | ZIP=1 | 基线=否 | 漏扫=否 | 渗透测试=否",
                terminal_output,
            )
            self.assertIn("HTML标识字段未识别到基线、漏扫或渗透测试", terminal_output)
            self.assertIn("缺少漏扫、渗透测试证明材料", terminal_output)
            self.assertIn(
                "[FX-12] ❌ 不通过 | 部门A/缺扫描报告 | 原因：缺少目标文档：扫描报告",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "三类齐全" / "FX-12" / "FX-12.json").is_file()
            )

    def test_fhx_03_compares_assessment_template_with_catalog_and_filing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            valid_unit = make_unit(root, "部门A", "标准类型")
            parent_child_unit = make_unit(root, "部门A", "父子类型")
            mismatch_unit = make_unit(root, "部门A", "备案不一致")
            invalid_unit = make_unit(root, "部门A", "系统名称类型")
            missing_unit = make_unit(root, "部门A", "仅证明材料")
            missing_filing_unit = make_unit(root, "部门A", "缺备案信息表")
            (valid_unit / "符合性评测表.md").write_text(
                FHX03_VALID_MARKDOWN, encoding="utf-8"
            )
            (valid_unit / "备案信息表.json").write_text(
                json.dumps({"网络/系统单元类型*": "融合网络"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (valid_unit / "符合性评测表证明材料.md").write_text(
                "通信网络安全防护符合性评测表－某错误类型", encoding="utf-8"
            )
            (parent_child_unit / "符合性评测表.md").write_text(
                "| 通信网络安全防护符合性评测表－信息服务业务系统 | |\n",
                encoding="utf-8",
            )
            (parent_child_unit / "备案信息表.json").write_text(
                json.dumps(
                    {"网络/系统单元类型*": "其他信息服务业务系统"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (mismatch_unit / "符合性评测表.md").write_text(
                FHX03_VALID_MARKDOWN, encoding="utf-8"
            )
            (mismatch_unit / "备案信息表.json").write_text(
                json.dumps(
                    {"网络/系统单元类型*": "电路域核心交换网"},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            (invalid_unit / "符合性评测表.md").write_text(
                FHX03_INVALID_MARKDOWN, encoding="utf-8"
            )
            (invalid_unit / "备案信息表.json").write_text(
                json.dumps({"网络/系统单元类型*": "某完整系统名称"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (missing_unit / "符合性评测表证明材料.md").write_text(
                "证明材料", encoding="utf-8"
            )
            (missing_unit / "备案信息表.json").write_text(
                json.dumps({"网络/系统单元类型*": "融合网络"}, ensure_ascii=False),
                encoding="utf-8",
            )
            (missing_filing_unit / "符合性评测表.md").write_text(
                FHX03_VALID_MARKDOWN, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FHX-03", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn(
                "[FHX-03] ✅ 通过 | 部门A/标准类型 | 网络类型=融合网络 | 备案类型=融合网络 | 标准匹配=是 | 备案一致=是 | 匹配方式=值一致",
                terminal_output,
            )
            self.assertIn(
                "[FHX-03] ✅ 通过 | 部门A/父子类型 | 网络类型=信息服务业务系统 | 备案类型=其他信息服务业务系统 | 标准匹配=是 | 备案一致=是 | 匹配方式=父子类",
                terminal_output,
            )
            self.assertIn(
                "[FHX-03] ❌ 不通过 | 部门A/备案不一致 | 网络类型=融合网络 | 备案类型=电路域核心交换网 | 标准匹配=是 | 备案一致=否 | 匹配方式=无",
                terminal_output,
            )
            self.assertIn(
                "[FHX-03] ❌ 不通过 | 部门A/系统名称类型 | 网络类型=某完整系统名称 | 备案类型=某完整系统名称 | 标准匹配=否 | 备案一致=未比较 | 匹配方式=无",
                terminal_output,
            )
            self.assertIn("网络类型“某完整系统名称”不在标准类型表中", terminal_output)
            self.assertIn("[FHX-03] ❌ 不通过 | 部门A/仅证明材料 | 原因：缺少目标文档：符合性评测表.md", terminal_output)
            self.assertIn(
                "[FHX-03] ❌ 不通过 | 部门A/缺备案信息表 | 原因：缺少目标文档：备案信息表",
                terminal_output,
            )
            fields_path = output / "部门A" / "标准类型" / "FHX-03" / "FHX-03.json"
            self.assertTrue(fields_path.is_file())

    def test_fhx_02_selects_assessment_table_and_reports_each_unit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            failed_unit = make_unit(root, "部门A", "系统1")
            passed_unit = make_unit(root, "部门A", "系统2")
            missing_unit = make_unit(root, "部门A", "系统3")
            (failed_unit / "符合性评测表.md").write_text(
                FHX02_INVALID_MARKDOWN, encoding="utf-8"
            )
            (failed_unit / "符合性评测表证明材料.md").write_text(
                "证明材料不参与答案检查", encoding="utf-8"
            )
            (passed_unit / "符合性评测表.md").write_text(
                FHX02_VALID_MARKDOWN, encoding="utf-8"
            )
            (missing_unit / "符合性评测表证明材料.md").write_text(
                "只有证明材料", encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FHX-02", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn("[FHX-02] ❌ 不通过 | 部门A/系统1", terminal_output)
            self.assertIn("题目=3 | 非法答案=2", terminal_output)
            self.assertIn("答案为其他文字×1、答案空白×1", terminal_output)
            self.assertIn("[FHX-02] ✅ 通过 | 部门A/系统2", terminal_output)
            self.assertIn("[FHX-02] ❌ 不通过 | 部门A/系统3", terminal_output)
            self.assertIn("缺少目标文档：符合性评测表.md", terminal_output)
            self.assertTrue((output / "部门A" / "系统1" / "FHX-02" / "FHX-02.json").is_file())

    def test_fx_03_batch_recovers_degraded_numbered_titles(self) -> None:
        complete = """#### 总体评价
|2. 资产识别|前页。 2.1. 业务识别 正文|
2.1.2. 业务重要性赋值 正文。
2.2.2. 资产价值赋值 正文。
|前页。 3. 威胁识别|
3.4. 威胁赋值说明 正文。
|很低 4. 已有安全措施有效性分析|
# 5. 脆弱性识别
## 5.3. 脆弱性赋值说明
# 6. 风险计算与评价
"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            valid_unit = make_unit(root, "部门A", "降级标题")
            missing_unit = make_unit(root, "部门A", "缺少报告")
            (valid_unit / "风险评估报告.md").write_text(complete, encoding="utf-8")
            (missing_unit / "定级报告.md").write_text("无关文档", encoding="utf-8")
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-03", output_dir=output)

            terminal_output = format_batch_summary(summary, output)
            self.assertIn(
                "[FX-03] ✅ 通过 | 部门A/降级标题 | 标题=7/7 | 报告=风险评估报告.md",
                terminal_output,
            )
            self.assertIn(
                "[FX-03] ❌ 不通过 | 部门A/缺少报告 | 原因：缺少目标文档：风险评估报告.md",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "降级标题" / "FX-03" / "FX-03.json").is_file()
            )

    def test_fx_13_and_fx_14_check_their_own_measure_types(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "测试集"
            passed_unit = make_unit(root, "部门A", "包含预防性措施")
            failed_unit = make_unit(root, "部门A", "只有保护性措施")
            missing_unit = make_unit(root, "部门A", "缺少报告")
            parse_error_unit = make_unit(root, "部门A", "报告解析失败")
            recovered_unit = make_unit(root, "部门A", "使用可用副本")
            (passed_unit / "风险评估报告.md").write_text(
                FX13_PASS_MARKDOWN, encoding="utf-8"
            )
            (failed_unit / "风险评估报告.md").write_text(
                "正文提到保护性安全措施，但不是类型列。\n" + FX13_FAIL_MARKDOWN,
                encoding="utf-8",
            )
            (missing_unit / "定级报告.md").write_text("无关文档", encoding="utf-8")
            (parse_error_unit / "风险评估报告.md").write_text(
                '<!-- PARSE_ERROR source="报告.docx" reason="malformed" -->\n'
                "# 风险评估报告解析失败\n",
                encoding="utf-8",
            )
            (recovered_unit / "风险评估报告.md").write_text(
                '<!-- PARSE_ERROR source="报告.docx" reason="malformed" -->\n'
                "# 风险评估报告解析失败\n",
                encoding="utf-8",
            )
            (recovered_unit / "风险评估报告（2）.md").write_text(
                FX13_PASS_MARKDOWN, encoding="utf-8"
            )
            output = Path(directory) / "output"

            summary = run_batch(dataset_dir=root, item_id="FX-13", output_dir=output)
            terminal_output = format_batch_summary(summary, output)

            self.assertIn(
                "[FX-13] ✅ 通过 | 部门A/包含预防性措施 | 类型列=是 | "
                "保护性安全措施=是 | 命中证据=1",
                terminal_output,
            )
            self.assertIn(
                "[FX-13] ❌ 不通过 | 部门A/只有保护性措施 | 类型列=是 | "
                "保护性安全措施=否 | 命中证据=0",
                terminal_output,
            )
            self.assertIn("类型”字段未包含“保护性安全措施", terminal_output)
            self.assertIn(
                "[FX-13] ❌ 不通过 | 部门A/缺少报告 | "
                "原因：缺少目标文档：风险评估报告.md",
                terminal_output,
            )
            self.assertIn(
                "[FX-13] ❌ 不通过 | 部门A/报告解析失败 | 原因：风险评估报告"
                "全部为解析失败存根",
                terminal_output,
            )
            self.assertIn(
                "[FX-13] ❌ 不通过 | 部门A/使用可用副本 | 原因：风险评估报告全部为解析失败存根",
                terminal_output,
            )
            self.assertTrue(
                (output / "部门A" / "包含预防性措施" / "FX-13" / "FX-13.json").is_file()
            )

            output_fx_14 = Path(directory) / "output_fx_14"
            summary_fx_14 = run_batch(
                dataset_dir=root, item_id="FX-14", output_dir=output_fx_14
            )
            terminal_fx_14 = format_batch_summary(summary_fx_14, output_fx_14)
            self.assertIn(
                "[FX-14] ❌ 不通过 | 部门A/包含预防性措施 | 类型列=是 | "
                "预防性安全措施=否 | 命中证据=0",
                terminal_fx_14,
            )
            self.assertIn(
                "[FX-14] ✅ 通过 | 部门A/只有保护性措施 | 类型列=是 | "
                "预防性安全措施=是 | 命中证据=1",
                terminal_fx_14,
            )
            self.assertTrue(
                (output_fx_14 / "部门A" / "只有保护性措施" / "FX-14" / "FX-14.json").is_file()
            )


if __name__ == "__main__":
    unittest.main()
