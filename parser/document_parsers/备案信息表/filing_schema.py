# -*- coding: utf-8 -*-
"""Schema-driven parser for the network-unit filing form family."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable

from office_source import Cell, Table


def label_id(text: str) -> str:
    text = (text or "").lower().replace("＊", "*")
    text = re.sub(r"[\s*]", "", text)
    text = text.replace("．", ".")
    return text.strip("：:")


def value_text(text: str) -> str:
    text = (text or "").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip()


@dataclass
class AuditEvent:
    code: str
    message: str
    severity: str = "warning"
    field_path: str = ""
    table_index: int | None = None
    row_index: int | None = None
    raw_text: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class ParseAudit:
    events: list[AuditEvent] = field(default_factory=list)
    checkbox_decisions: list[dict] = field(default_factory=list)
    filtered_records: list[dict] = field(default_factory=list)
    schema_placeholders: list[dict] = field(default_factory=list)

    def add(self, code: str, message: str, **kwargs) -> None:
        self.events.append(AuditEvent(code=code, message=message, **kwargs))

    @property
    def needs_review(self) -> bool:
        return any(item.severity in {"warning", "error"} for item in self.events)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    aliases: tuple[str, ...]

    def matches(self, text: str) -> bool:
        value = label_id(text)
        return any(value == label_id(alias) or value.startswith(label_id(alias)) for alias in self.aliases)


@dataclass(frozen=True)
class SectionSpec:
    key: str
    aliases: tuple[str, ...]
    fields: tuple[FieldSpec, ...]
    core_fields: tuple[str, ...]
    allow_no_sentinel: bool = False

    def matches(self, text: str) -> bool:
        value = label_id(text)
        return any(value == label_id(alias) for alias in self.aliases)


@dataclass
class Choice:
    label: str
    selected: bool | None
    attached_value: str
    evidence_source: str
    confidence: float
    page: int | None


SCALAR_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec("网络/系统单元名称*", ("网络/系统单元名称",)),
    FieldSpec("投入运行时间*", ("投入运行时间",)),
    FieldSpec("填表人*", ("填表人",)),
    FieldSpec("填表时间*", ("填表时间",)),
    FieldSpec("网络/系统单元类型*", ("网络/系统单元类型",)),
    FieldSpec("联系人信息*·姓 名*", ("姓名",)),
    FieldSpec("联系人信息*·职务/职称*", ("职务/职称",)),
    FieldSpec("联系人信息*·办公电话", ("办公电话",)),
    FieldSpec("联系人信息*·电子邮件*", ("电子邮件",)),
    FieldSpec("联系人信息*·移动电话*", ("移动电话",)),
    FieldSpec("服务情况*·服务范围*", ("服务范围",)),
    FieldSpec("服务情况*·服务对象*", ("服务对象",)),
    FieldSpec("专家评审*", ("专家评审",)),
    FieldSpec("所要申报的安全等级*", ("所要申报的安全等级",)),
    FieldSpec("定级时间*", ("定级时间",)),
    FieldSpec("IPV6改造情况*", ("ipv6改造情况",)),
    FieldSpec("IPV6改造情况说明", ("ipv6改造情况说明",)),
    FieldSpec("是否拥有公网IP地址*", ("是否拥有公网ip地址",)),
    FieldSpec("是否存在主要的协议和端口（公网）*", ("是否存在主要的协议和端口（公网）",)),
    FieldSpec("网络用户信息存储处理情况", ("网络用户信息存储处理情况",)),
    FieldSpec("接入带宽", ("接入带宽",)),
    FieldSpec("信息服务系统是否使用域名", ("信息服务系统是否使用域名",)),
    FieldSpec("是否使用.cn域名", ("是否使用.cn域名", "是否使用cn域名")),
    FieldSpec("网络单元所用域名", ("网络单元所用域名",)),
)

SCALAR_BY_KEY = {item.key: item for item in SCALAR_FIELDS}


SECTIONS: tuple[SectionSpec, ...] = (
    SectionSpec(
        "网络运维负责人信息（可添加多个）",
        ("网络运维负责人信息（可添加多个）",),
        (
            FieldSpec("网络运维责任人*", ("网络运维责任人",)),
            FieldSpec("部门名称*", ("部门名称",)),
            FieldSpec("联系方式*", ("联系方式",)),
        ),
        ("网络运维责任人*",),
    ),
    SectionSpec(
        "主要硬件使用情况",
        ("主要硬件使用情况",),
        (
            FieldSpec("类型*", ("类型",)),
            FieldSpec("品牌*", ("品牌",)),
            FieldSpec("型号*", ("型号",)),
            FieldSpec("数量(台) *", ("数量(台)", "数量（台）")),
            FieldSpec("维护方式*", ("维护方式",)),
            FieldSpec("已运行时间（天）*", ("已运行时间（天）", "已运行时间")),
            FieldSpec("所在机房位置*", ("所在机房位置",)),
            FieldSpec("负责人*", ("负责人",)),
            FieldSpec("联系方式*", ("联系方式",)),
            FieldSpec("存在问题*", ("存在问题",)),
        ),
        ("类型*", "品牌*", "型号*", "数量(台) *", "所在机房位置*"),
    ),
    SectionSpec(
        "安全系统使用情况",
        ("安全系统使用情况",),
        (
            FieldSpec("类型*", ("类型",)),
            FieldSpec("品牌*", ("品牌",)),
            FieldSpec("型号*（定制化软件系统请填写定制）", ("型号",)),
            FieldSpec("数量(台) *", ("数量(台)", "数量（台）")),
            FieldSpec("维护方式*", ("维护方式",)),
            FieldSpec("已运行时间（天）*", ("已运行时间（天）", "已运行时间")),
            FieldSpec("所在机房位置*", ("所在机房位置",)),
            FieldSpec("负责人*", ("负责人",)),
            FieldSpec("联系方式*", ("联系方式",)),
            FieldSpec("存在问题*", ("存在问题",)),
        ),
        ("类型*", "品牌*", "型号*（定制化软件系统请填写定制）", "数量(台) *", "所在机房位置*"),
    ),
    SectionSpec(
        "基础软件使用情况",
        ("基础软件使用情况",),
        (
            FieldSpec("类型*", ("类型",)),
            FieldSpec("厂商*", ("厂商",)),
            FieldSpec("当前版本*", ("当前版本",)),
            FieldSpec("数量（套）*", ("数量（套）", "数量(套)")),
            FieldSpec("维护方式*", ("维护方式",)),
            FieldSpec("已运行时间（天）*", ("已运行时间（天）", "已运行时间")),
            FieldSpec("所在机房位置*", ("所在机房位置",)),
            FieldSpec("所在设备*（承载此软件的业务设备类别、名称）", ("所在设备",)),
            FieldSpec("正版软件*", ("正版软件",)),
            FieldSpec("存在问题*", ("存在问题",)),
        ),
        ("类型*", "厂商*", "当前版本*", "数量（套）*", "所在机房位置*", "所在设备*（承载此软件的业务设备类别、名称）"),
    ),
    SectionSpec(
        "应用软件情况",
        ("应用软件情况",),
        (
            FieldSpec("名称*", ("名称",)),
            FieldSpec("当前版本*", ("当前版本",)),
            FieldSpec("研发方*", ("研发方",)),
            FieldSpec("所在机房位置*", ("所在机房位置",)),
            FieldSpec("所在设备*（承载应用软件的业务设备类别、名称）", ("所在设备",)),
            FieldSpec("数量（套）*", ("数量（套）", "数量(套)")),
            FieldSpec("维护方式*", ("维护方式",)),
            FieldSpec("已运行时间（天）*", ("已运行时间（天）", "已运行时间")),
            FieldSpec("域名*", ("域名",)),
            FieldSpec("端口*", ("端口",)),
            FieldSpec("url*", ("url",)),
            FieldSpec("中间件类型*", ("中间件类型",)),
            FieldSpec("存在问题*", ("存在问题",)),
        ),
        ("名称*", "当前版本*", "所在机房位置*", "所在设备*（承载应用软件的业务设备类别、名称）", "数量（套）*"),
    ),
    SectionSpec(
        "网络单元使用的主要协议和端口（公网，和资产清单公网IP对应）",
        ("网络单元使用的主要协议和端口（公网，和资产清单公网ip对应）",),
        (
            FieldSpec("端口*", ("端口",)),
            FieldSpec("端口描述（协议）*", ("端口描述（协议）", "端口描述")),
            FieldSpec("对应IP段*", ("对应ip段",)),
        ),
        ("端口*", "端口描述（协议）*", "对应IP段*"),
        allow_no_sentinel=True,
    ),
    SectionSpec(
        "处理网络数据信息",
        ("处理网络数据信息",),
        (
            FieldSpec("网络数据类型（资产数据、管理数据、监测数据、运营数据、其他 ）", ("网络数据类型",)),
            FieldSpec("数量（M/兆）", ("数量（m/兆）", "数量(m/兆)")),
        ),
        ("网络数据类型（资产数据、管理数据、监测数据、运营数据、其他 ）", "数量（M/兆）"),
        allow_no_sentinel=True,
    ),
)


SINGLE_CHOICE_SCALARS = {
    "服务情况*·服务范围*",
    "服务情况*·服务对象*",
    "专家评审*",
    "所要申报的安全等级*",
    "IPV6改造情况*",
    "是否拥有公网IP地址*",
    "是否存在主要的协议和端口（公网）*",
    "网络用户信息存储处理情况",
    "信息服务系统是否使用域名",
    "是否使用.cn域名",
}


def _field_for_text(fields: Iterable[FieldSpec], text: str) -> FieldSpec | None:
    matches = [item for item in fields if item.matches(text)]
    if not matches:
        return None
    # Prefer the most specific alias when e.g. "IPV6改造情况说明" and
    # "IPV6改造情况" share a prefix.
    return max(matches, key=lambda item: max(len(label_id(a)) for a in item.aliases))


def _section_for_row(cells: list[Cell]) -> SectionSpec | None:
    for cell in cells:
        for section in SECTIONS:
            if section.matches(cell.normalized_text):
                return section
    return None


def _choice_value(text: str, audit: ParseAudit, field_path: str, cell: Cell) -> str:
    text = value_text(text)
    if not any(mark in text for mark in "☑□�"):
        return text
    pieces = list(re.finditer(r"([☑□�])([^☑□�]*)", text, re.S))
    selected: list[str] = []
    choices: list[Choice] = []
    unknown = False
    for marker_index, match in enumerate(pieces):
        marker, body = match.group(1), value_text(match.group(2))
        body = re.sub(r"[：:]\s*$", "", body).strip()
        evidence = (
            cell.symbol_evidence[marker_index]
            if marker_index < len(cell.symbol_evidence)
            else None
        )
        label, attached = _choice_label_and_value(body)
        choices.append(
            Choice(
                label=label,
                selected=True if marker == "☑" else (False if marker == "□" else None),
                attached_value=attached,
                evidence_source=evidence.source if evidence else "unknown",
                confidence=evidence.confidence if evidence else 0.0,
                page=evidence.page if evidence else None,
            )
        )
        if marker == "☑" and body:
            selected.append(body)
        elif marker == "�":
            unknown = True
    decision = {
        "field_path": field_path,
        "table_index": cell.table_index,
        "row_index": cell.row_index,
        "raw_text": cell.raw_text,
        "selected": selected,
        "unknown": unknown,
        "choices": [item.__dict__.copy() for item in choices],
        "evidence": [item.__dict__.copy() for item in cell.symbol_evidence],
    }
    audit.checkbox_decisions.append(decision)
    if unknown:
        audit.add(
            "CHECKBOX_UNKNOWN",
            "复选框符号无法确定，字段按保守策略留空",
            field_path=field_path,
            table_index=cell.table_index,
            row_index=cell.row_index,
            raw_text=cell.raw_text,
        )
        return ""
    return "；".join(selected)


def _choice_label_and_value(body: str) -> tuple[str, str]:
    body = value_text(body)
    # Keep the full body for business JSON, but expose attached values
    # separately in the audit so ports, URLs and organisation names cannot be
    # lost when an unchecked alternative is discarded.
    match = re.match(r"^(.*?)(?:\n?[：:]\s*)[（(]\s*(.*?)\s*[）)]\s*$", body, re.S)
    if match:
        return value_text(match.group(1)), value_text(match.group(2))
    return body, ""


def _record_is_meaningful(section: SectionSpec, record: dict[str, str]) -> bool:
    # Source fidelity takes precedence over template heuristics: '/', default
    # problem text and XX/XXX location examples are still source content.  A
    # row is discarded only when every mapped business field is truly empty.
    return any(value_text(value) for value in record.values())


def _header_map(section: SectionSpec, cells: list[Cell]) -> dict[str, tuple[int, int]]:
    result: dict[str, tuple[int, int]] = {}
    for cell in cells:
        field = _field_for_text(section.fields, cell.normalized_text)
        if field:
            result[field.key] = (cell.column_start, cell.column_end)
    return result


def _overlap(cell: Cell, start: int, end: int) -> int:
    return max(0, min(cell.column_end, end) - max(cell.column_start, start))


def _row_record(
    section: SectionSpec,
    headers: dict[str, tuple[int, int]],
    cells: list[Cell],
    audit: ParseAudit,
) -> dict[str, str]:
    record = {field.key: "" for field in section.fields}
    for field in section.fields:
        bounds = headers.get(field.key)
        if not bounds:
            continue
        candidates = [cell for cell in cells if _overlap(cell, *bounds) > 0]
        if not candidates:
            continue
        cell = max(candidates, key=lambda item: (_overlap(item, *bounds), -abs(item.column_start - bounds[0])))
        record[field.key] = _choice_value(
            cell.normalized_text,
            audit,
            f"{section.key}[].{field.key}",
            cell,
        )
    return record


def _next_value(cells: list[Cell], index: int) -> Cell | None:
    label_cell = cells[index]
    for candidate in cells[index + 1 :]:
        if candidate.column_start < label_cell.column_end:
            continue
        if _field_for_text(SCALAR_FIELDS, candidate.normalized_text):
            return None
        return candidate
    return None


def _parse_scalar_row(
    cells: list[Cell],
    result: dict,
    audit: ParseAudit,
    occurrences: dict[str, int],
) -> bool:
    found = False
    row_ids = {label_id(cell.normalized_text) for cell in cells}
    agency_row = any(value.startswith("域名注册服务机构信息") for value in row_ids) or any(
        value == "机构名称" for value in row_ids
    )
    cn_records_row = any(value.startswith(".cn域名相关记录") or value.startswith("cn域名相关记录") for value in row_ids)
    for index, cell in enumerate(cells):
        field = _field_for_text(SCALAR_FIELDS, cell.normalized_text)
        if field is None:
            continue
        if field.key == "联系人信息*·办公电话" and agency_row:
            continue
        value_cell = _next_value(cells, index)
        value = _choice_value(
            value_cell.normalized_text if value_cell else "",
            audit,
            field.key,
            value_cell or cell,
        )
        occurrences[field.key] = occurrences.get(field.key, 0) + 1
        if occurrences[field.key] > 1:
            audit.add(
                "SOURCE_DUPLICATE_FIELD",
                f"源文档重复出现字段 {field.key}",
                field_path=field.key,
                table_index=cell.table_index,
                row_index=cell.row_index,
                raw_text=cell.raw_text,
            )
        if not result.get(field.key) or value:
            result[field.key] = value
        found = True

    # Two compound rows need contextual field names that are intentionally
    # not part of the global scalar alias list.
    for index, cell in enumerate(cells):
        current = label_id(cell.normalized_text)
        if agency_row and current in {"机构名称", "办公电话"}:
            key = "域名注册服务机构信息·机构名称" if current == "机构名称" else "域名注册服务机构信息·办公电话"
        elif cn_records_row and current in {"ns记录", "a记录"}:
            key = ".cn域名相关记录·NS记录" if current == "ns记录" else ".cn域名相关记录·A记录"
        else:
            continue
        value_cell = _next_value(cells, index)
        result[key] = value_text(value_cell.normalized_text if value_cell else "")
        found = True
    return found


def empty_result() -> dict:
    keys = [field.key for field in SCALAR_FIELDS]
    keys.extend(
        [
            ".cn域名相关记录·NS记录",
            ".cn域名相关记录·A记录",
            "域名注册服务机构信息·机构名称",
            "域名注册服务机构信息·办公电话",
        ]
    )
    result = {key: "" for key in keys}
    # The business JSON is a fixed Schema: even when every source row in a
    # section is blank/template content, retain the section key with an empty
    # array.  Downstream callers can therefore distinguish "no valid record"
    # from an outdated parser that never emitted the field.
    result.update({section.key: [] for section in SECTIONS})
    return result


def parse_filing_tables(tables: list[Table]) -> tuple[dict, ParseAudit]:
    result = empty_result()
    audit = ParseAudit()
    occurrences: dict[str, int] = {}

    for table in tables:
        active: SectionSpec | None = None
        headers: dict[str, tuple[int, int]] = {}
        for cells in table.rows:
            cells = sorted(cells, key=lambda item: item.column_start)
            if not cells or not any(cell.normalized_text for cell in cells):
                continue
            if any(label_id(cell.normalized_text) == "备注" for cell in cells):
                active, headers = None, {}
                continue

            section = _section_for_row(cells)
            if section is not None:
                active = section
                candidate = _header_map(section, cells)
                if len(candidate) >= 2:
                    headers = candidate
                else:
                    headers = {}
                    audit.add(
                        "SECTION_HEADER_INCOMPLETE",
                        f"区块 {section.key} 未识别到完整表头",
                        field_path=section.key,
                        table_index=table.table_index,
                        row_index=cells[0].row_index,
                    )
                continue

            # Scalar rows terminate a preceding record section.
            if any(_field_for_text(SCALAR_FIELDS, cell.normalized_text) for cell in cells):
                active, headers = None, {}
                _parse_scalar_row(cells, result, audit, occurrences)
                continue

            if active is not None:
                repeated = _header_map(active, cells)
                if len(repeated) >= 2:
                    headers = repeated
                    continue
                if not headers:
                    audit.add(
                        "UNMAPPED_SECTION_ROW",
                        f"区块 {active.key} 中的行无可用表头，未写入主 JSON",
                        severity="info",
                        field_path=active.key,
                        table_index=table.table_index,
                        row_index=cells[0].row_index,
                        raw_text=" | ".join(cell.normalized_text for cell in cells),
                    )
                    continue
                record = _row_record(active, headers, cells, audit)
                if _record_is_meaningful(active, record):
                    result.setdefault(active.key, []).append(record)
                else:
                    audit.filtered_records.append(
                        {
                            "section": active.key,
                            "table_index": table.table_index,
                            "row_index": cells[0].row_index,
                            "record": record,
                            "reason": "所有映射后的业务字段均为空",
                        }
                    )
                continue

            if not _parse_scalar_row(cells, result, audit, occurrences):
                audit.add(
                    "UNMAPPED_ROW",
                    "未识别行已进入审计，未生成业务字段",
                    severity="info",
                    table_index=table.table_index,
                    row_index=cells[0].row_index,
                    raw_text=" | ".join(cell.normalized_text for cell in cells),
                )

    _validate_result(result, audit, occurrences)
    _fill_empty_section_schemas(result, audit)
    return result, audit


def _fill_empty_section_schemas(result: dict, audit: ParseAudit) -> None:
    """Expose every nested field without turning source templates into data.

    Empty record sections receive one all-empty Schema object.  The audit
    explicitly identifies these objects as generated placeholders, allowing
    downstream code to distinguish them from parsed business records.
    """
    for section in SECTIONS:
        if result.get(section.key):
            continue
        placeholder = {field.key: "" for field in section.fields}
        result[section.key] = [placeholder]
        audit.schema_placeholders.append(
            {
                "section": section.key,
                "record_index": 0,
                "reason": "区块无有效业务记录，为保留完整 Schema 生成全字段空对象",
                "fields": [field.key for field in section.fields],
            }
        )


def _validate_result(result: dict, audit: ParseAudit, occurrences: dict[str, int]) -> None:
    required = (
        "网络/系统单元名称*",
        "投入运行时间*",
        "填表人*",
        "填表时间*",
        "网络/系统单元类型*",
    )
    for key in required:
        if not result.get(key):
            audit.add("REQUIRED_FIELD_EMPTY", f"必填字段为空: {key}", field_path=key)

    unit_type = result.get("网络/系统单元类型*", "")
    unit_name = result.get("网络/系统单元名称*", "")
    if "参照" in unit_type or "参考备注" in unit_type:
        audit.add(
            "SOURCE_PLACEHOLDER_VALUE",
            "网络/系统单元类型仍为模板提示",
            field_path="网络/系统单元类型*",
            raw_text=unit_type,
        )
    if "同步网" in unit_name and unit_type and "同步" not in unit_type:
        audit.add(
            "SOURCE_TYPE_MISMATCH",
            "系统名称为同步网，但网络类型未包含同步网",
            field_path="网络/系统单元类型*",
            raw_text=unit_type,
        )

    for key in SINGLE_CHOICE_SCALARS:
        value = result.get(key, "")
        if isinstance(value, str) and "；" in value:
            audit.add(
                "MULTIPLE_SELECTED",
                "单选字段检测到多个已选项，按原文保留",
                field_path=key,
                raw_text=value,
                severity="info",
            )
