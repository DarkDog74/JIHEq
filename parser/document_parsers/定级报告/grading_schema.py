# -*- coding: utf-8 -*-
"""Fixed-schema parser for the China Mobile grading-report template family."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from source import DocumentBlock, DocumentStructure, TableData, clean_text


PARSER_VERSION = "1.0.0"


def _section() -> dict:
    return {"内容": "", "图片": [], "表格": []}


def empty_business_schema() -> dict:
    """Return every supported field, including fields absent from the source."""
    return {
        "报告基本信息": {
            "报告名称": "",
            "版本": "",
            "报告日期": "",
            "系统名称": "",
            "文档名称": "",
            "作者": "",
            "类别": "",
            "子类别": "",
            "文件名称": "",
            "摘要": "",
        },
        "修订记录": [],
        "基本情况": {
            "系统概述": _section(),
            "业务/应用范围": _section(),
            "服务范围": _section(),
            "用户类型": _section(),
            "所属单位基本情况": _section(),
            "安全管理架构": _section(),
            "物理环境": _section(),
            "网络拓扑结构": _section(),
            "硬件设备部署情况": _section(),
            "网络边界划分": _section(),
            "云服务平台使用情况": _section(),
            "其他内容": _section(),
        },
        "安全等级的确定": {
            "赋值理由": {
                "社会影响力": {"赋值": None, "理由": "", "图片": [], "表格": []},
                "规模和服务范围": {"赋值": None, "理由": "", "图片": [], "表格": []},
                "提供服务的重要性": {"赋值": None, "理由": "", "图片": [], "表格": []},
            },
            "采用定级方法及选择权重理由": _section(),
            "计算参数": {
                "I": None,
                "R": None,
                "V": None,
                "alpha": None,
                "beta": None,
                "gamma": None,
                "k": None,
                "原文": "",
            },
            "安全等级映射关系": [],
            "安全保护等级": {"等级": None, "结论": "", "图片": [], "表格": []},
            "其他内容": _section(),
        },
        "资产信息": {"表头": [], "记录": []},
        "其他表格": [],
        "正文结构": [],
    }


@dataclass
class SchemaAudit:
    issues: list[dict] = field(default_factory=list)
    filtered_empty_rows: list[dict] = field(default_factory=list)
    retained_partial_rows: list[dict] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    table_usage: dict[int, str] = field(default_factory=dict)

    def issue(self, code: str, message: str, severity: str = "warning", **details: Any) -> None:
        self.issues.append(
            {"code": code, "message": message, "severity": severity, "details": details}
        )

    @property
    def needs_review(self) -> bool:
        return bool(self.missing_fields) or any(
            item["severity"] in {"warning", "error"} for item in self.issues
        )


def _normalized_label(text: str) -> str:
    value = re.sub(r"\s+", "", clean_text(text))
    value = re.sub(r"^[（(]?[一二三四五六七八九十0-9]+[）)、.．\s]+", "", value)
    value = value.rstrip("：:")
    return value


def _compact_nonempty(row: list[str]) -> list[str]:
    return [clean_text(value) for value in row if clean_text(value)]


def _table_contains(table: TableData, *needles: str) -> bool:
    for row in table.matrix:
        joined = "|".join(_normalized_label(value) for value in row if value)
        if all(needle in joined for needle in needles):
            return True
    return False


def parse_document_info(table: TableData) -> dict[str, str]:
    aliases = {
        "文档名称": "文档名称",
        "作者": "作者",
        "类别": "类别",
        "子类别": "子类别",
        "文件名称": "文件名称",
        "摘要": "摘要",
    }
    values = {value: "" for value in aliases.values()}
    for row in table.matrix:
        normalized_row = [_normalized_label(value) for value in row]
        if any("修订历史" in value for value in normalized_row) or (
            "版本号" in normalized_row and "日期" in normalized_row
        ):
            break
        nonempty_indexes = [index for index, value in enumerate(row) if clean_text(value)]
        if not nonempty_indexes:
            continue
        for position, index in enumerate(nonempty_indexes):
            key = _normalized_label(row[index])
            canonical = aliases.get(key)
            if canonical is None:
                continue
            following = [
                clean_text(row[next_index])
                for next_index in nonempty_indexes[position + 1 :]
                if clean_text(row[next_index])
            ]
            values[canonical] = following[0] if following else ""
            break
    return values


def parse_revision_table(table: TableData) -> list[dict]:
    result: list[dict] = []
    header: dict[str, int] = {}
    for row in table.matrix:
        normalized = [_normalized_label(value) for value in row]
        if "版本号" in normalized and "日期" in normalized:
            header = {}
            for index, name in enumerate(normalized):
                if name == "版本号":
                    header["版本号"] = index
                elif name == "日期":
                    header["日期"] = index
                elif name in {"作者", "修改人"}:
                    header["作者/修改人"] = index
                elif name == "审阅人":
                    header["审阅人"] = index
                elif name == "摘要":
                    header["摘要"] = index
            continue
        if not header:
            continue
        version = next(
            (
                clean_text(value)
                for value in row
                if re.fullmatch(r"[Vv]?(?:\d+)(?:\.\d+)+", clean_text(value))
            ),
            "",
        )
        if not version:
            continue
        record = {"版本号": version}
        for field_name in ("日期", "作者/修改人", "审阅人", "摘要"):
            index = header.get(field_name, -1)
            record[field_name] = clean_text(row[index]) if 0 <= index < len(row) else ""
        result.append(record)
    return result


def _unique_headers(headers: list[str]) -> list[str]:
    result: list[str] = []
    counts: dict[str, int] = {}
    for index, original in enumerate(headers):
        base = clean_text(original) or f"未命名列{index + 1}"
        counts[base] = counts.get(base, 0) + 1
        result.append(base if counts[base] == 1 else f"{base}_{counts[base]}")
    return result


def parse_asset_table(table: TableData, audit: SchemaAudit) -> tuple[list[str], list[dict]]:
    header_row = -1
    first_column = 0
    last_column = table.column_count - 1
    for row_index, row in enumerate(table.matrix):
        normalized = [_normalized_label(value) for value in row]
        if "资产IP" in normalized:
            header_row = row_index
            occupied = [index for index, value in enumerate(row) if clean_text(value)]
            if occupied:
                first_column, last_column = min(occupied), max(occupied)
            break
    if header_row < 0:
        return [], []

    headers = _unique_headers(table.matrix[header_row][first_column : last_column + 1])
    records: list[dict] = []
    for row_index, row in enumerate(table.matrix[header_row + 1 :], start=header_row + 1):
        values = [
            clean_text(row[index]) if index < len(row) else ""
            for index in range(first_column, last_column + 1)
        ]
        if not any(values):
            audit.filtered_empty_rows.append(
                {"表格编号": table.table_index, "行号": row_index, "原因": "整行完全空白"}
            )
            continue
        # Important: a partially populated row is always retained.
        if not all(values):
            audit.retained_partial_rows.append(
                {
                    "表格编号": table.table_index,
                    "行号": row_index,
                    "非空字段数": sum(bool(value) for value in values),
                    "字段总数": len(values),
                }
            )
        records.append({header: values[index] for index, header in enumerate(headers)})
    return headers, records


def parse_mapping_table(table: TableData) -> list[dict]:
    header_row = -1
    key_index = value_index = -1
    for row_index, row in enumerate(table.matrix):
        normalized = [_normalized_label(value) for value in row]
        for index, value in enumerate(normalized):
            if "安全等级值" in value:
                key_index = index
            if value == "安全等级":
                value_index = index
        if key_index >= 0 and value_index >= 0:
            header_row = row_index
            break
    if header_row < 0:
        return []
    records = []
    for row in table.matrix[header_row + 1 :]:
        left = clean_text(row[key_index]) if key_index < len(row) else ""
        right = clean_text(row[value_index]) if value_index < len(row) else ""
        if left or right:
            records.append({"安全等级值k": left, "安全等级": right})
    return records


def _heading_kind(text: str) -> tuple[str, str] | None:
    label = _normalized_label(text)
    if label.endswith("基本情况") and label != "所属单位基本情况":
        return "basic", ""
    if ("安全等级的确定" in label or label.endswith("安全等级确定")) and "保护等级" not in label:
        return "grading", ""
    aliases = {
        "系统概述": ("basic", "系统概述"),
        "业务/应用范围": ("basic", "业务/应用范围"),
        "业务应用范围": ("basic", "业务/应用范围"),
        "服务范围": ("basic", "服务范围"),
        "用户类型": ("basic", "用户类型"),
        "所属单位基本情况": ("basic", "所属单位基本情况"),
        "安全管理架构": ("basic", "安全管理架构"),
        "管理架构": ("basic", "安全管理架构"),
        "物理环境": ("basic", "物理环境"),
        "网络拓扑结构": ("basic", "网络拓扑结构"),
        "网络结构": ("basic", "网络拓扑结构"),
        "硬件设备部署情况": ("basic", "硬件设备部署情况"),
        "网络边界划分": ("basic", "网络边界划分"),
        "云服务平台使用情况": ("basic", "云服务平台使用情况"),
        "社会影响力赋值": ("grading", "社会影响力"),
        "规模和服务范围赋值": ("grading", "规模和服务范围"),
        "提供服务的重要性赋值": ("grading", "提供服务的重要性"),
        "所提供服务的重要性赋值": ("grading", "提供服务的重要性"),
        "采用定级方法及选择权重理由": ("grading", "采用定级方法及选择权重理由"),
    }
    if label in aliases:
        return aliases[label]
    if label.endswith("安全保护等级"):
        return "grading", "安全保护等级"
    if label in {"管理情况", "技术情况"}:
        return "basic", ""
    if label == "赋值理由":
        return "grading", ""
    return None


def _field_object(result: dict, major: str, field_name: str) -> dict:
    if major == "basic":
        return result["基本情况"][field_name or "其他内容"]
    grading = result["安全等级的确定"]
    if field_name in {"社会影响力", "规模和服务范围", "提供服务的重要性"}:
        return grading["赋值理由"][field_name]
    if field_name == "安全保护等级":
        return grading["安全保护等级"]
    return grading[field_name or "其他内容"]


def _append_text(field: dict, text: str) -> None:
    key = "理由" if "理由" in field else "结论" if "结论" in field else "内容"
    field[key] = "\n".join(value for value in (field[key], clean_text(text)) if value)


def _append_images(field: dict, block: DocumentBlock) -> None:
    for image in block.images:
        value = image.to_dict()
        if value not in field["图片"]:
            field["图片"].append(value)


def _extract_cover(result: dict, structure: DocumentStructure) -> None:
    info = result["报告基本信息"]
    first_table_block = min(
        (block.block_index for block in structure.blocks if block.kind == "table"),
        default=10**9,
    )
    cover_texts = [
        block.text
        for block in structure.blocks
        if block.kind == "paragraph" and block.block_index < first_table_block and block.text
    ]
    for text in cover_texts:
        if "定级报告" in text and not text.startswith(("附：", "附件：", "附:")):
            info["报告名称"] = text
            break
    for text in cover_texts:
        match = re.fullmatch(r"(?i)Ver\.?\s*([0-9]+(?:\.[0-9]+)+)", text.replace(" ", ""))
        if match:
            info["版本"] = match.group(1)
            break
    for text in cover_texts:
        if re.search(r"20\d{2}\s*年", text):
            info["报告日期"] = clean_text(text)
    if info["报告名称"]:
        info["系统名称"] = re.sub(r"[-—]?定级报告.*$", "", info["报告名称"]).strip()


def _extract_values(result: dict, all_text: str, audit: SchemaAudit) -> None:
    grading = result["安全等级的确定"]
    reasons = grading["赋值理由"]
    for name, item in reasons.items():
        match = re.search(r"赋值(?:应)?为\s*([1-5](?:\.\d+)?)", item["理由"])
        if match:
            number = float(match.group(1))
            item["赋值"] = int(number) if number.is_integer() else number

    method_text = grading["采用定级方法及选择权重理由"]["内容"]
    params = grading["计算参数"]
    for variable, raw in re.findall(
        r"(?<![A-Za-z])([IRV])\s*=\s*([1-5](?:\.\d+)?)", method_text, flags=re.I
    ):
        number = float(raw)
        params[variable.upper()] = int(number) if number.is_integer() else number
    for name, symbol in (("alpha", "α"), ("beta", "β"), ("gamma", "γ")):
        match = re.search(rf"{symbol}\s*=\s*([0-9]+(?:/[0-9]+)?(?:\.\d+)?)", method_text)
        if match:
            params[name] = match.group(1)
    combined_weight = re.search(
        r"α\s*=\s*β\s*=\s*γ\s*=\s*([0-9]+(?:/[0-9]+)?(?:\.\d+)?)", method_text
    )
    if combined_weight:
        params["alpha"] = params["beta"] = params["gamma"] = combined_weight.group(1)
    k_matches = re.findall(
        r"(?<![A-Za-z])k\s*=\s*([0-9]+(?:\.\d+)?)", method_text, flags=re.I
    )
    if k_matches:
        number = float(k_matches[-1])
        params["k"] = int(number) if number.is_integer() else number
    params["原文"] = method_text

    conclusion = grading["安全保护等级"]["结论"]
    level_patterns = [
        r"安全等级确定为\s*第?\s*([1-5])\s*级",
        r"安全等级为\s*第?\s*([1-5])\s*级",
    ]
    candidates: list[str] = []
    for pattern in level_patterns:
        candidates.extend(re.findall(pattern, conclusion))
    if not candidates:
        for pattern in level_patterns:
            candidates.extend(re.findall(pattern, all_text))
    if candidates:
        grading["安全保护等级"]["等级"] = int(candidates[-1])

    expected = {
        "I": reasons["社会影响力"]["赋值"],
        "R": reasons["规模和服务范围"]["赋值"],
        "V": reasons["提供服务的重要性"]["赋值"],
    }
    for variable, semantic_value in expected.items():
        if semantic_value is not None and params[variable] is not None and semantic_value != params[variable]:
            audit.issue(
                "PARAMETER_SEMANTIC_CONFLICT",
                f"计算参数 {variable} 与对应赋值理由不一致，已分别原样保留",
                "warning",
                variable=variable,
                reason_value=semantic_value,
                calculation_value=params[variable],
            )


def _validate_revision_order(records: list[dict], audit: SchemaAudit) -> None:
    previous: datetime | None = None
    for record in records:
        raw = record.get("日期", "")
        parsed = None
        for pattern in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
            try:
                parsed = datetime.strptime(raw.replace(" ", ""), pattern)
                break
            except ValueError:
                continue
        if parsed and previous and parsed < previous:
            audit.issue(
                "REVISION_DATE_OUT_OF_ORDER",
                "修订记录日期没有按版本顺序递增",
                "warning",
                version=record.get("版本号", ""),
                date=raw,
            )
        if parsed:
            previous = parsed


def _record_missing_fields(result: dict, audit: SchemaAudit) -> None:
    info = result["报告基本信息"]
    required_info = ("报告名称", "版本", "报告日期", "系统名称", "文档名称")
    for field_name in required_info:
        if not info[field_name]:
            audit.missing_fields.append(f"报告基本信息.{field_name}")
    required_basic = (
        "系统概述",
        "业务/应用范围",
        "服务范围",
        "用户类型",
        "所属单位基本情况",
        "安全管理架构",
        "物理环境",
        "网络拓扑结构",
        "硬件设备部署情况",
        "网络边界划分",
    )
    for field_name in required_basic:
        section = result["基本情况"][field_name]
        if not section["内容"] and not section["图片"] and not section["表格"]:
            audit.missing_fields.append(f"基本情况.{field_name}")
    for field_name, item in result["安全等级的确定"]["赋值理由"].items():
        if not item["理由"]:
            audit.missing_fields.append(f"安全等级的确定.赋值理由.{field_name}.理由")
        if item["赋值"] is None:
            audit.missing_fields.append(f"安全等级的确定.赋值理由.{field_name}.赋值")
    if result["安全等级的确定"]["安全保护等级"]["等级"] is None:
        audit.missing_fields.append("安全等级的确定.安全保护等级.等级")


def parse_grading_report(structure: DocumentStructure) -> tuple[dict, SchemaAudit]:
    result = empty_business_schema()
    audit = SchemaAudit()
    _extract_cover(result, structure)

    metadata_table: TableData | None = None
    asset_tables: set[int] = set()
    mapping_tables: set[int] = set()
    for table in structure.tables:
        if _table_contains(table, "资产IP"):
            asset_tables.add(table.table_index)
            audit.table_usage[table.table_index] = "资产信息"
        elif _table_contains(table, "安全等级值", "安全等级"):
            mapping_tables.add(table.table_index)
            audit.table_usage[table.table_index] = "安全等级映射关系"
        elif metadata_table is None and _table_contains(table, "文档名称"):
            metadata_table = table
            audit.table_usage[table.table_index] = "文档信息及修订记录"

    if metadata_table is not None:
        parsed_info = parse_document_info(metadata_table)
        result["报告基本信息"].update(parsed_info)
        if result["报告基本信息"]["报告名称"] in {"", "定级报告"}:
            result["报告基本信息"]["报告名称"] = parsed_info["文档名称"]
        if not result["报告基本信息"]["系统名称"] and parsed_info["文档名称"]:
            result["报告基本信息"]["系统名称"] = re.sub(
                r"[-—]?定级报告.*$", "", parsed_info["文档名称"]
            ).strip()
        result["修订记录"] = parse_revision_table(metadata_table)
    else:
        audit.issue("DOCUMENT_INFO_TABLE_MISSING", "未识别到文档信息及修订记录表", "warning")

    for table in structure.tables:
        if table.table_index in asset_tables:
            headers, records = parse_asset_table(table, audit)
            if not result["资产信息"]["表头"]:
                result["资产信息"]["表头"] = headers
            elif headers != result["资产信息"]["表头"]:
                audit.issue(
                    "ASSET_HEADER_CHANGED",
                    "同一报告内的资产表表头发生变化，记录仍全部保留",
                    "warning",
                    table_index=table.table_index,
                    headers=headers,
                )
            result["资产信息"]["记录"].extend(records)
        elif table.table_index in mapping_tables:
            result["安全等级的确定"]["安全等级映射关系"].extend(
                parse_mapping_table(table)
            )

    major = ""
    current_field = ""
    current_path: list[str] = []
    all_paragraph_texts: list[str] = []
    for block in structure.blocks:
        if block.kind == "paragraph":
            if block.text:
                all_paragraph_texts.append(block.text)
            heading = _heading_kind(block.text) if block.text else None
            if heading:
                major, current_field = heading
                current_path = ["基本情况" if major == "basic" else "安全等级的确定"]
                if current_field:
                    current_path.append(current_field)
                result["正文结构"].append(
                    {
                        "序号": block.block_index,
                        "类型": "标题",
                        "章节路径": current_path,
                        "文本": block.text,
                        "样式": block.style_name,
                        "图片": [image.to_dict() for image in block.images],
                    }
                )
                continue
            if major:
                target = _field_object(result, major, current_field)
                if block.text:
                    _append_text(target, block.text)
                _append_images(target, block)
            if block.text or block.images:
                result["正文结构"].append(
                    {
                        "序号": block.block_index,
                        "类型": "段落",
                        "章节路径": current_path,
                        "文本": block.text,
                        "样式": block.style_name,
                        "图片": [image.to_dict() for image in block.images],
                    }
                )
        elif block.table is not None:
            table = block.table
            usage = audit.table_usage.get(table.table_index, "章节表格" if major else "其他表格")
            data_path = ""
            if usage in {"文档信息及修订记录", "资产信息", "安全等级映射关系"}:
                data_path = usage
            elif major:
                target = _field_object(result, major, current_field)
                target["表格"].append(
                    {"表格编号": table.table_index, "数据": table.matrix}
                )
                audit.table_usage[table.table_index] = "章节表格"
                data_path = ".".join(current_path + ["表格"])
            else:
                result["其他表格"].append(
                    {"表格编号": table.table_index, "数据": table.matrix}
                )
                audit.table_usage[table.table_index] = "其他表格"
                data_path = "其他表格"
            result["正文结构"].append(
                {
                    "序号": block.block_index,
                    "类型": "表格",
                    "章节路径": current_path,
                    "表格编号": table.table_index,
                    "用途": usage,
                    "行数": len(table.matrix),
                    "列数": table.column_count,
                    "数据路径": data_path,
                }
            )

    all_text = "\n".join(all_paragraph_texts)
    _extract_values(result, all_text, audit)
    _validate_revision_order(result["修订记录"], audit)
    _record_missing_fields(result, audit)
    if audit.missing_fields:
        audit.issue(
            "SOURCE_FIELDS_MISSING",
            "源报告存在未填写或未识别的固定字段；主 JSON 已保留空字段",
            "warning",
            fields=audit.missing_fields,
        )
    if not result["资产信息"]["记录"]:
        audit.issue("ASSET_LIST_EMPTY", "资产清单没有有效记录", "warning")
    return result, audit
