from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from xml.etree import ElementTree as ET


GROUND_TRUTH_FILENAME = "ground_truth.v1.json"
DEFAULT_EXCLUDED_ITEMS = frozenset({"DJ-06"})
ITEM_ID_RE = re.compile(r"^([A-Z][A-Z0-9]*-[0-9]{2})(?:\s|$)")
GROUP_COUNT_RE = re.compile(r"^(.*?)([0-9]+)$")
SYSTEM_SUFFIX_RE = re.compile(r"-+[0-9]+$")
SYSTEM_YEAR_SUFFIX_RE = re.compile(r"-20[0-9]{2}$")

_MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


class LabelExtractionError(ValueError):
    """工作簿内容无法安全转换成 ground_truth.v1.json。"""


@dataclass(frozen=True)
class AuditItem:
    item_id: str
    name: str
    category: str
    source_row: int


@dataclass(frozen=True)
class SystemColumn:
    column_index: int
    workbook_name: str
    group_label: str


@dataclass(frozen=True)
class ExportResult:
    department: str
    scope: str
    target: Path
    system_count: int
    item_count: int
    created: bool


def _clean_text(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\u3000", " ").split())


def _cell_column_index(reference: str) -> int:
    match = re.match(r"^([A-Z]+)", reference.upper())
    if match is None:
        raise LabelExtractionError(f"Excel 单元格坐标无效：{reference!r}")
    index = 0
    for char in match.group(1):
        index = index * 26 + ord(char) - ord("A") + 1
    return index - 1


def _read_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(name))
    return [
        "".join(node.text or "" for node in item.findall(f".//{{{_MAIN_NS}}}t"))
        for item in root.findall(f"{{{_MAIN_NS}}}si")
    ]


def _sheet_target(
    archive: zipfile.ZipFile, sheet_name: str | None
) -> tuple[str, str]:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        relation.attrib["Id"]: relation.attrib["Target"]
        for relation in relationships.findall(f"{{{_PKG_REL_NS}}}Relationship")
    }
    sheets = workbook.findall(f".//{{{_MAIN_NS}}}sheet")
    if not sheets:
        raise LabelExtractionError("Excel 中没有工作表")
    selected = None
    if sheet_name is None:
        selected = sheets[0]
    else:
        selected = next((sheet for sheet in sheets if sheet.attrib.get("name") == sheet_name), None)
        if selected is None:
            available = "、".join(sheet.attrib.get("name", "") for sheet in sheets)
            raise LabelExtractionError(
                f"未找到工作表 {sheet_name!r}；可用工作表：{available}"
            )
    relationship_id = selected.attrib.get(f"{{{_REL_NS}}}id")
    target = targets.get(relationship_id or "")
    if not target:
        raise LabelExtractionError("无法定位工作表 XML")
    if target.startswith("/"):
        member = target.lstrip("/")
    else:
        member = posixpath.normpath(posixpath.join("xl", target))
    return selected.attrib.get("name", ""), member


def _cell_value(cell: ET.Element, shared_strings: Sequence[str]) -> str:
    cell_type = cell.attrib.get("t", "")
    if cell_type == "inlineStr":
        return "".join(
            node.text or "" for node in cell.findall(f".//{{{_MAIN_NS}}}t")
        )
    value_node = cell.find(f"{{{_MAIN_NS}}}v")
    value = value_node.text if value_node is not None and value_node.text is not None else ""
    if cell_type == "s" and value:
        try:
            return shared_strings[int(value)]
        except (IndexError, ValueError) as error:
            raise LabelExtractionError("Excel 共享字符串索引无效") from error
    if cell_type == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def load_xlsx_rows(path: Path, *, sheet_name: str | None = None) -> tuple[str, list[list[str]]]:
    path = Path(path).expanduser().resolve()
    if path.suffix.casefold() != ".xlsx":
        raise LabelExtractionError("当前脚本只支持 .xlsx 文件")
    if not path.is_file():
        raise FileNotFoundError(f"Excel 文件不存在：{path}")
    try:
        with zipfile.ZipFile(path) as archive:
            selected_name, member = _sheet_target(archive, sheet_name)
            shared_strings = _read_shared_strings(archive)
            sheet = ET.fromstring(archive.read(member))
    except (KeyError, zipfile.BadZipFile, ET.ParseError) as error:
        raise LabelExtractionError(f"Excel 文件结构无效：{error}") from error

    rows_by_index: dict[int, dict[int, str]] = {}
    max_row = -1
    max_column = -1
    for cell in sheet.findall(f".//{{{_MAIN_NS}}}c"):
        reference = cell.attrib.get("r", "")
        row_match = re.search(r"([0-9]+)$", reference)
        if row_match is None:
            raise LabelExtractionError(f"Excel 单元格坐标无效：{reference!r}")
        row_index = int(row_match.group(1)) - 1
        column_index = _cell_column_index(reference)
        rows_by_index.setdefault(row_index, {})[column_index] = _cell_value(
            cell, shared_strings
        )
        max_row = max(max_row, row_index)
        max_column = max(max_column, column_index)
    if max_row < 0 or max_column < 0:
        raise LabelExtractionError("工作表没有数据")

    rows = [["" for _ in range(max_column + 1)] for _ in range(max_row + 1)]
    for row_index, cells in rows_by_index.items():
        for column_index, value in cells.items():
            rows[row_index][column_index] = value
    return selected_name, rows


def classify_label(value: object) -> str:
    text = _clean_text(value)
    if not text:
        raise LabelExtractionError("标签单元格为空")
    if text.startswith("不适用"):
        return "not_applicable"
    failed_prefixes = (
        "不符合",
        "不一致",
        "不齐全",
        "未识别",
        "无法检测",
        "无法确认",
        "无法判断",
        "不通过",
        "失败",
        "否",
    )
    if text.startswith(failed_prefixes):
        return "failed"
    passed_prefixes = ("符合", "一致", "通过", "正确", "齐全", "是")
    if text.startswith(passed_prefixes):
        return "passed"
    raise LabelExtractionError(f"无法识别标签结论：{text[:80]!r}")


def _find_layout(rows: Sequence[Sequence[str]]) -> tuple[int, int, int, list[SystemColumn]]:
    for header_index, row in enumerate(rows[:20]):
        normalized = [_clean_text(value) for value in row]
        if "稽核文件" not in normalized or "检查项" not in normalized or "判定逻辑" not in normalized:
            continue
        if header_index == 0:
            raise LabelExtractionError("系统分组行缺失")
        category_column = normalized.index("稽核文件")
        item_column = normalized.index("检查项")
        logic_column = normalized.index("判定逻辑")
        group_row = rows[header_index - 1]
        group_label = ""
        systems: list[SystemColumn] = []
        for column_index in range(logic_column + 1, len(row)):
            if column_index < len(group_row) and _clean_text(group_row[column_index]):
                group_label = _clean_text(group_row[column_index])
            workbook_name = _clean_text(row[column_index])
            if not workbook_name:
                continue
            if not group_label:
                raise LabelExtractionError(
                    f"第 {column_index + 1} 列系统缺少部门分组名称"
                )
            systems.append(SystemColumn(column_index, workbook_name, group_label))
        if not systems:
            raise LabelExtractionError("未找到任何系统标签列")
        return header_index, category_column, item_column, systems
    raise LabelExtractionError("未找到包含“稽核文件/检查项/判定逻辑”的表头行")


def _parse_items(
    rows: Sequence[Sequence[str]],
    *,
    header_index: int,
    category_column: int,
    item_column: int,
    excluded_items: set[str],
) -> list[AuditItem]:
    category = ""
    items: list[AuditItem] = []
    seen: set[str] = set()
    for row_index, row in enumerate(rows[header_index + 1 :], start=header_index + 1):
        if category_column < len(row) and _clean_text(row[category_column]):
            category = _clean_text(row[category_column])
        raw_name = _clean_text(row[item_column] if item_column < len(row) else "")
        match = ITEM_ID_RE.match(raw_name)
        if match is None:
            continue
        item_id = match.group(1)
        if item_id in excluded_items:
            continue
        if item_id in seen:
            raise LabelExtractionError(f"检查项编号重复：{item_id}")
        if not category:
            raise LabelExtractionError(f"{item_id} 缺少稽核文件分类")
        seen.add(item_id)
        items.append(AuditItem(item_id, raw_name, category, row_index))
    if not items:
        raise LabelExtractionError("工作表中没有可导出的检查项")
    return items


def _group_base_and_count(group_label: str) -> tuple[str, int | None]:
    match = GROUP_COUNT_RE.match(_clean_text(group_label))
    if match is None:
        return _clean_text(group_label), None
    return match.group(1), int(match.group(2))


def _department_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value))
    text = re.sub(r"[\s_\-—–]+", "", text)
    return text[:-1] if text.endswith("部") else text


def _resolve_department(root: Path, group_label: str) -> Path:
    group_base, _ = _group_base_and_count(group_label)
    candidates = [
        candidate
        for candidate in root.iterdir()
        if candidate.is_dir()
        and not candidate.name.startswith(".")
        and not candidate.name.endswith("label")
        and _department_key(candidate.name) == _department_key(group_base)
    ]
    if len(candidates) != 1:
        names = "、".join(candidate.name for candidate in candidates) or "无"
        raise LabelExtractionError(
            f"部门分组 {group_label!r} 无法唯一匹配网管中心子目录；候选：{names}"
        )
    return candidates[0]


def _system_key(value: str) -> str:
    text = unicodedata.normalize("NFKC", _clean_text(value))
    text = SYSTEM_SUFFIX_RE.sub("", text)
    text = SYSTEM_YEAR_SUFFIX_RE.sub("", text)
    return re.sub(r"[\s_\-—–]+", "", text).casefold()


def _exact_system_key(value: str) -> str:
    """保留数字后缀，用于优先区分 -3-01、-3-02 等扩充样本。"""
    text = unicodedata.normalize("NFKC", _clean_text(value))
    return re.sub(r"\s+", "", text).casefold()


def _system_directories(department: Path) -> list[Path]:
    return [
        candidate
        for candidate in department.iterdir()
        if candidate.is_dir()
        and not candidate.name.startswith(".")
        and not candidate.name.endswith("_anydoc解析结果")
        and candidate.name not in {"output", "解析结果", "稽核结果"}
    ]


def _resolve_system(department: Path, workbook_name: str) -> Path:
    directories = _system_directories(department)
    exact_key = _exact_system_key(workbook_name)
    exact_candidates = [
        candidate
        for candidate in directories
        if _exact_system_key(candidate.name) == exact_key
    ]
    if len(exact_candidates) == 1:
        return exact_candidates[0]
    if len(exact_candidates) > 1:
        names = "、".join(candidate.name for candidate in exact_candidates)
        raise LabelExtractionError(
            f"系统 {workbook_name!r} 在 {department} 下存在多个完整名称匹配；候选：{names}"
        )

    key = _system_key(workbook_name)
    candidates = [
        candidate
        for candidate in directories
        if _system_key(candidate.name) == key
    ]
    if len(candidates) != 1:
        names = "、".join(candidate.name for candidate in candidates) or "无"
        raise LabelExtractionError(
            f"系统 {workbook_name!r} 无法在 {department} 下唯一匹配；候选：{names}"
        )
    return candidates[0]


def build_payloads(
    rows: Sequence[Sequence[str]],
    root_dir: Path,
    *,
    excluded_items: Iterable[str] = DEFAULT_EXCLUDED_ITEMS,
) -> dict[Path, dict[str, object]]:
    root = Path(root_dir).expanduser().resolve()
    if not root.is_dir():
        raise NotADirectoryError(f"网管中心目录不存在：{root}")
    excluded = {_clean_text(item).upper() for item in excluded_items if _clean_text(item)}
    header_index, category_column, item_column, system_columns = _find_layout(rows)
    items = _parse_items(
        rows,
        header_index=header_index,
        category_column=category_column,
        item_column=item_column,
        excluded_items=excluded,
    )

    grouped_columns: dict[str, list[SystemColumn]] = {}
    for system in system_columns:
        grouped_columns.setdefault(system.group_label, []).append(system)

    payloads: dict[Path, dict[str, object]] = {}
    for group_label, columns in grouped_columns.items():
        department = _resolve_department(root, group_label)
        _, expected_count = _group_base_and_count(group_label)
        if expected_count is not None and expected_count != len(columns):
            raise LabelExtractionError(
                f"部门分组 {group_label!r} 声明 {expected_count} 个系统，实际有 {len(columns)} 列"
            )
        labels: dict[str, dict[str, str]] = {}
        for system in columns:
            system_directory = _resolve_system(department, system.workbook_name)
            item_labels: dict[str, str] = {}
            for item in items:
                row = rows[item.source_row]
                value = row[system.column_index] if system.column_index < len(row) else ""
                try:
                    item_labels[item.item_id] = classify_label(value)
                except LabelExtractionError as error:
                    raise LabelExtractionError(
                        f"{group_label}/{system.workbook_name}/{item.item_id}（Excel第{item.source_row + 1}行）：{error}"
                    ) from error
            labels[system_directory.name] = item_labels

        target = root / f"{department.name}label" / GROUND_TRUTH_FILENAME
        if target in payloads:
            raise LabelExtractionError(f"多个分组将写入同一文件：{target}")
        payloads[target] = {
            "schema_version": 1,
            "kind": "audit_ground_truth",
            "source_name": department.name,
            "scope": group_label,
            "items": [
                {
                    "item_id": item.item_id,
                    "name": item.name,
                    "category": item.category,
                }
                for item in items
            ],
            "systems": labels,
        }
    return payloads


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    temporary_name = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_name = handle.name
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def export_ground_truth_labels(
    xlsx_path: Path,
    root_dir: Path,
    *,
    sheet_name: str | None = None,
    overwrite: bool = False,
    dry_run: bool = False,
    excluded_items: Iterable[str] = DEFAULT_EXCLUDED_ITEMS,
) -> list[ExportResult]:
    _, rows = load_xlsx_rows(xlsx_path, sheet_name=sheet_name)
    payloads = build_payloads(rows, root_dir, excluded_items=excluded_items)
    existing = [path for path in payloads if path.exists()]
    if existing and not overwrite and not dry_run:
        raise FileExistsError(
            "目标标签已存在，请增加 --overwrite：" + "、".join(str(path) for path in existing)
        )

    results: list[ExportResult] = []
    for target, payload in payloads.items():
        created = not target.exists()
        if not dry_run:
            _atomic_write_json(target, payload)
        results.append(
            ExportResult(
                department=str(payload["source_name"]),
                scope=str(payload["scope"]),
                target=target,
                system_count=len(payload["systems"]),
                item_count=len(payload["items"]),
                created=created,
            )
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="从稽核项标注 Excel 提取 JSON v1，并写入对应的 <部门>label 文件夹。"
    )
    parser.add_argument("--xlsx", required=True, type=Path, help="稽核项标注 .xlsx 文件")
    parser.add_argument("--root-dir", required=True, type=Path, help="网管中心根目录")
    parser.add_argument("--sheet", help="工作表名称；默认读取第一个工作表")
    parser.add_argument("--overwrite", action="store_true", help="覆盖已有 ground_truth.v1.json")
    parser.add_argument("--dry-run", action="store_true", help="只校验和展示目标，不写文件")
    parser.add_argument(
        "--exclude-item",
        action="append",
        default=[],
        metavar="ITEM_ID",
        help="额外排除稽核项，可重复使用；DJ-06 默认排除",
    )
    parser.add_argument(
        "--include-item",
        action="append",
        default=[],
        metavar="ITEM_ID",
        help="取消默认排除的稽核项，可重复使用",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    excluded = set(DEFAULT_EXCLUDED_ITEMS)
    excluded.update(_clean_text(item).upper() for item in args.exclude_item)
    excluded.difference_update(_clean_text(item).upper() for item in args.include_item)
    try:
        results = export_ground_truth_labels(
            args.xlsx,
            args.root_dir,
            sheet_name=args.sheet,
            overwrite=args.overwrite,
            dry_run=args.dry_run,
            excluded_items=excluded,
        )
    except (FileNotFoundError, NotADirectoryError, FileExistsError, LabelExtractionError) as error:
        print(f"提取失败：{error}")
        return 2

    mode = "校验" if args.dry_run else "写入"
    print(f"{mode}完成：部门={len(results)}，默认排除项={','.join(sorted(excluded)) or '无'}")
    for result in results:
        action = "将新建" if args.dry_run and result.created else "将覆盖" if args.dry_run else "已写入"
        print(
            f"[{result.department}] scope={result.scope} | 系统={result.system_count} | "
            f"稽核项={result.item_count} | {action}：{result.target}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
