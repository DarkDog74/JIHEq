from __future__ import annotations

import unittest

from extractors.bean_01 import BEAN01ExtractionError, extract_bean_01_data


class BEAN01ExtractorTests(unittest.TestCase):
    def test_checks_starred_flat_fields_and_list_records(self) -> None:
        payload = {
            "系统名称*": "",
            "联系人信息*·姓 名*": "张三",
            "办公电话": "",
            "设备": [
                {"类型*": "服务器", "品牌*": ""},
                {"类型*": "交换机", "品牌*": "华为"},
            ],
        }

        result = extract_bean_01_data(payload)

        self.assertEqual(result["必填项总数"], 6)
        self.assertEqual(result["已填写必填项数"], 4)
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["系统名称", "设备[第1条].品牌"],
        )

    def test_parent_group_star_does_not_make_optional_leaf_required(self) -> None:
        result = extract_bean_01_data(
            {
                "联系人信息*·姓名*": "张三",
                "联系人信息*·办公电话": "",
                "联系人信息*·电子邮件*": "a@example.com",
            }
        )
        self.assertEqual(result["必填项总数"], 2)
        self.assertEqual(result["缺失必填项"], [])

    def test_blank_placeholder_record_reports_each_starred_column(self) -> None:
        result = extract_bean_01_data(
            {
                "应用软件情况": [
                    {"名称*": "", "当前版本*": None, "存在问题*": "未发现问题"}
                ]
            }
        )
        self.assertEqual(result["必填项总数"], 3)
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["应用软件情况[第1条].名称", "应用软件情况[第1条].当前版本"],
        )

    def test_non_target_section_still_skips_blank_siblings_after_no(self) -> None:
        result = extract_bean_01_data(
            {
                "其他明细": [
                    {
                        "类型*": "无",
                        "品牌*": "无",
                        "型号*": "无",
                        "维护方式*": "",
                        "负责人*": "无",
                    }
                ]
            }
        )
        self.assertEqual(result["必填项总数"], 4)
        self.assertEqual(result["已填写必填项数"], 4)
        self.assertEqual(result["缺失必填项"], [])

    def test_non_target_no_exemption_is_limited_to_current_record(self) -> None:
        result = extract_bean_01_data(
            {
                "其他明细": [
                    {"类型*": "无", "品牌*": ""},
                    {"类型*": "防火墙", "品牌*": ""},
                ]
            }
        )
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["其他明细[第2条].品牌"],
        )

    def test_six_target_sections_require_all_first_record_business_fields(self) -> None:
        sections = (
            "主要硬件使用情况",
            "安全系统使用情况",
            "基础软件使用情况",
            "应用软件情况",
            "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）",
            "处理网络数据信息",
        )
        for section in sections:
            with self.subTest(section=section):
                payload = {
                    "系统名称*": "系统A",
                    section: [
                        {
                            "类型*": "无",
                            "无星号说明": "",
                            "_解析行号": "12",
                            "_解析信息": {"内部字段*": ""},
                        }
                    ],
                }
                if "主要协议和端口" in section:
                    payload["是否存在主要的协议和端口（公网）*"] = "是"
                result = extract_bean_01_data(payload)
                self.assertEqual(
                    [item["字段"] for item in result["缺失必填项"]],
                    [f"{section}[第1条].无星号说明"],
                )

    def test_target_first_record_accepts_complete_nonblank_values(self) -> None:
        result = extract_bean_01_data(
            {
                "处理网络数据信息": [
                    {
                        "数据量": 0,
                        "是否处理": False,
                        "补充说明": "无",
                        "不适用项": "/",
                    }
                ]
            }
        )
        self.assertEqual(result["必填项总数"], 4)
        self.assertEqual(result["已填写必填项数"], 4)
        self.assertEqual(result["缺失必填项"], [])

    def test_target_first_record_allows_blank_remote_local_maintenance(self) -> None:
        result = extract_bean_01_data(
            {
                "主要硬件使用情况": [
                    {
                        "类型*": "服务器",
                        "品牌*": "华为",
                        "维护方式*": "",
                    }
                ]
            }
        )
        self.assertEqual(result["必填项总数"], 2)
        self.assertEqual(result["已填写必填项数"], 2)
        self.assertEqual(result["缺失必填项"], [])

    def test_target_later_populated_record_still_requires_maintenance(self) -> None:
        result = extract_bean_01_data(
            {
                "主要硬件使用情况": [
                    {"类型*": "服务器", "品牌*": "华为", "维护方式*": ""},
                    {"类型*": "交换机", "品牌*": "华为", "维护方式*": ""},
                ]
            }
        )
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["主要硬件使用情况[第2条].维护方式"],
        )

    def test_top_level_maintenance_field_is_not_exempt(self) -> None:
        result = extract_bean_01_data({"维护方式*": ""})
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["维护方式"],
        )

    def test_target_empty_list_and_empty_first_record_are_missing(self) -> None:
        cases = ([], {"类型*": "", "说明": "", "_解析行号": 10})
        for records in cases:
            with self.subTest(records=records):
                result = extract_bean_01_data(
                    {"系统名称*": "系统A", "主要硬件使用情况": records}
                )
                self.assertEqual(
                    result["缺失必填项"],
                    [
                        {
                            "字段": "主要硬件使用情况",
                            "原始值": str(records),
                            "原因": "首条记录未填写",
                        }
                    ],
                )

    def test_target_later_all_blank_row_is_skipped(self) -> None:
        result = extract_bean_01_data(
            {
                "安全系统使用情况": [
                    {"类型*": "防火墙", "品牌*": "华为", "说明": "正常"},
                    {"类型*": "", "品牌*": "", "说明": "", "_行号": 2},
                ]
            }
        )
        self.assertEqual(result["缺失必填项"], [])

    def test_target_later_partly_filled_rows_require_every_business_field(self) -> None:
        trigger_values = (0, False, "无", "/")
        records = [
            {"类型*": "防火墙", "品牌*": "华为", "说明": "正常"},
            {"类型*": "", "品牌*": "", "说明": ""},
            *[
                {"类型*": value, "品牌*": "", "说明": "完整"}
                for value in trigger_values
            ],
        ]
        result = extract_bean_01_data({"安全系统使用情况": records})
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            [
                f"安全系统使用情况[第{index}条].品牌"
                for index in range(3, 7)
            ],
        )

    def test_target_rows_do_not_use_no_value_exemption(self) -> None:
        result = extract_bean_01_data(
            {
                "应用软件情况": [
                    {"名称*": "无", "版本*": ""},
                    {"名称*": "无", "版本*": ""},
                ]
            }
        )
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            [
                "应用软件情况[第1条].版本",
                "应用软件情况[第2条].版本",
            ],
        )

    def test_later_no_value_does_not_exempt_missing_identity_fields(self) -> None:
        result = extract_bean_01_data(
            {
                "基础软件使用情况": [
                    {"类型*": "数据库系统", "当前版本*": "", "存在问题*": "无"}
                ]
            }
        )
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            ["基础软件使用情况[第1条].当前版本"],
        )

    def test_skips_records_explicitly_marked_as_generated_schema_placeholders(self) -> None:
        result = extract_bean_01_data(
            {
                "_解析信息": {
                    "模板兼容状态": "compatible",
                    "合成占位区块": ["应用软件情况"],
                },
                "系统名称*": "系统A",
                "应用软件情况": [
                    {"名称*": "", "当前版本*": "", "存在问题*": ""}
                ],
            }
        )
        self.assertEqual(result["必填项总数"], 1)
        self.assertEqual(result["缺失必填项"], [])

    def test_recurses_into_nested_objects(self) -> None:
        result = extract_bean_01_data(
            {"联系人": {"姓名*": "张三", "电子邮件*": ""}}
        )
        self.assertEqual(result["缺失必填项"][0]["字段"], "联系人.电子邮件")

    def test_zero_false_no_and_slash_are_not_blank(self) -> None:
        result = extract_bean_01_data(
            {
                "数量*": 0,
                "是否*": False,
                "说明*": "无",
                "不适用*": "/",
            }
        )
        self.assertEqual(result["已填写必填项数"], 4)
        self.assertEqual(result["缺失必填项"], [])

    def test_date_placeholder_and_partial_date_are_incomplete(self) -> None:
        result = extract_bean_01_data(
            {
                "投入运行时间*": "年 月 日",
                "填表时间*": "2025年 月 日",
                "定级时间*": "2025年8月1日",
            }
        )

        self.assertEqual(result["必填项总数"], 3)
        self.assertEqual(result["已填写必填项数"], 1)
        self.assertEqual(
            result["缺失必填项"],
            [
                {
                    "字段": "投入运行时间",
                    "原始值": "年 月 日",
                    "原因": "日期未填写完整",
                },
                {
                    "字段": "填表时间",
                    "原始值": "2025年 月 日",
                    "原因": "日期未填写完整",
                },
            ],
        )

    def test_accepts_supported_valid_date_formats(self) -> None:
        valid_dates = (
            "2024 年 11 月 15日",
            "2019/03/05",
            "2025-08-01",
            "2025.8.1",
        )
        for value in valid_dates:
            with self.subTest(value=value):
                result = extract_bean_01_data({"定级时间*": value})
                self.assertEqual(result["已填写必填项数"], 1)
                self.assertEqual(result["缺失必填项"], [])

    def test_rejects_calendar_invalid_date(self) -> None:
        result = extract_bean_01_data({"定级时间*": "2025年2月30日"})

        self.assertEqual(
            result["缺失必填项"],
            [
                {
                    "字段": "定级时间",
                    "原始值": "2025年2月30日",
                    "原因": "日期无效",
                }
            ],
        )

    def test_duration_field_is_not_treated_as_calendar_date(self) -> None:
        result = extract_bean_01_data({"已运行时间（天）*": "1275"})

        self.assertEqual(result["已填写必填项数"], 1)
        self.assertEqual(result["缺失必填项"], [])

    def test_cleans_stars_and_spacing_from_reported_paths(self) -> None:
        result = extract_bean_01_data({"数量(台) *": ""})
        self.assertEqual(result["缺失必填项"][0]["字段"], "数量(台)")

    def test_ignores_empty_optional_lists(self) -> None:
        result = extract_bean_01_data({"系统名称*": "系统A", "可选明细": []})
        self.assertEqual(result["必填项总数"], 1)
        self.assertEqual(result["缺失必填项"], [])

    def test_rejects_payload_without_starred_fields(self) -> None:
        with self.assertRaisesRegex(BEAN01ExtractionError, "未.*找到任何带星号"):
            extract_bean_01_data({"可选字段": ""})

    def test_public_protocol_details_are_conditional_when_gate_is_no(self) -> None:
        result = extract_bean_01_data(
            {
                "系统名称*": "5G新通话",
                "是否存在主要的协议和端口（公网）*": "否",
                "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
                    {"端口*": "", "端口描述（协议）*": "", "对应IP段*": ""}
                ],
            }
        )
        self.assertEqual(result["必填项总数"], 2)
        self.assertEqual(result["缺失必填项"], [])

    def test_public_protocol_gate_no_skips_even_partly_filled_later_rows(self) -> None:
        result = extract_bean_01_data(
            {
                "系统名称*": "系统A",
                "是否存在主要的协议和端口（公网）*": "不适用",
                "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
                    {"端口*": "443", "协议": ""},
                    {"端口*": "80", "协议": ""},
                ],
            }
        )
        self.assertEqual(result["必填项总数"], 2)
        self.assertEqual(result["缺失必填项"], [])

    def test_public_protocol_details_are_required_when_gate_is_yes(self) -> None:
        result = extract_bean_01_data(
            {
                "系统名称*": "系统A",
                "是否存在主要的协议和端口（公网）*": "是",
                "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）": [
                    {
                        "端口*": "443",
                        "端口描述（协议）*": "",
                        "对应IP段*": "10.0.0.1",
                        "无星号说明": "",
                    }
                ],
            }
        )
        self.assertEqual(
            [item["字段"] for item in result["缺失必填项"]],
            [
                "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）[第1条].端口描述（协议）",
                "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）[第1条].无星号说明",
            ],
        )

    def test_incompatible_template_requests_manual_review(self) -> None:
        result = extract_bean_01_data(
            {
                "_解析信息": {
                    "模板类型": "legacy_lbs",
                    "模板兼容状态": "needs_review",
                    "说明": "识别为旧版备案模板",
                },
                "网络/系统单元名称*": "",
            }
        )
        self.assertTrue(result["需人工复核"])
        self.assertEqual(result["缺失必填项"], [])


if __name__ == "__main__":
    unittest.main()
