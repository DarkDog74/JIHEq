"""Parse scan-report ZIP archives without extracting their full contents."""

from __future__ import annotations

import codecs
import json
import re
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable


MATERIAL_TYPES = ("基线", "漏扫", "渗透测试")
SCAN_ARCHIVE_KEYWORDS = ("扫描", "漏扫", "漏洞", "弱口令", "渗透", "基线")
MAX_ENTRY_SIZE = 20 * 1024 * 1024

_CHARSET_RE = re.compile(
    br"charset\s*=\s*[\"']?([A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
_WINDOW_DATA_RE = re.compile(r"window\.data\s*=\s*")


def _normalize(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _preferred_html_entry(infos: Iterable[zipfile.ZipInfo]) -> zipfile.ZipInfo | None:
    html_entries = [
        info
        for info in infos
        if not info.is_dir() and info.filename.casefold().endswith((".html", ".htm"))
    ]
    if not html_entries:
        return None
    index_entries = [
        info
        for info in html_entries
        if Path(info.filename).name.casefold() in {"index.html", "index.htm"}
    ]
    candidates = index_entries or html_entries
    return min(
        candidates,
        key=lambda info: (len(Path(info.filename).parts), info.filename.casefold()),
    )


def is_scan_archive_candidate(path: Path) -> bool:
    """Recognize HTML report ZIPs and named scan archives that lack HTML."""
    path = Path(path)
    if path.suffix.casefold() != ".zip":
        return False
    if any(keyword in str(path) for keyword in SCAN_ARCHIVE_KEYWORDS):
        return True
    try:
        with zipfile.ZipFile(path) as archive:
            return _preferred_html_entry(archive.infolist()) is not None
    except (OSError, zipfile.BadZipFile):
        return False


def _decode_html(data: bytes) -> tuple[str, str]:
    candidates: list[str] = []
    if data.startswith(codecs.BOM_UTF8):
        candidates.append("utf-8-sig")
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        candidates.append("utf-16")
    charset = _CHARSET_RE.search(data[:8192])
    if charset is not None:
        candidates.append(charset.group(1).decode("ascii", errors="ignore"))
    candidates.extend(("utf-8-sig", "gb18030"))

    attempted: set[str] = set()
    for encoding in candidates:
        normalized = encoding.casefold()
        if not encoding or normalized in attempted:
            continue
        attempted.add(normalized)
        try:
            return data.decode(encoding), encoding
        except (LookupError, UnicodeDecodeError):
            continue
    raise UnicodeError("HTML编码无法识别（已尝试UTF-8和GB18030）")


class _IdentityParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str]]] = []
        self.scripts: list[str] = []
        self.title_parts: list[str] = []
        self._row: list[tuple[str, str]] | None = None
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._script_parts: list[str] | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        if lowered == "tr":
            self._row = []
        elif lowered in {"th", "td"} and self._row is not None:
            self._cell_tag = lowered
            self._cell_parts = []
        elif lowered == "script":
            self._script_parts = []
        elif lowered == "title":
            self._in_title = True

    def handle_data(self, data: str) -> None:
        if self._cell_tag is not None:
            self._cell_parts.append(data)
        if self._script_parts is not None:
            self._script_parts.append(data)
        if self._in_title:
            self.title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in {"th", "td"} and self._cell_tag == lowered:
            if self._row is not None:
                self._row.append((lowered, _normalize("".join(self._cell_parts))))
            self._cell_tag = None
            self._cell_parts = []
        elif lowered == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None
        elif lowered == "script" and self._script_parts is not None:
            self.scripts.append("".join(self._script_parts))
            self._script_parts = None
        elif lowered == "title":
            self._in_title = False


_STATIC_FIELD_NAMES = {
    "任务名称": "任务名称",
    "任务类型": "任务类型",
    "漏洞扫描模板": "漏洞扫描模板",
    "扫描模板": "漏洞扫描模板",
    "报告名称": "报告标题",
    "报告标题": "报告标题",
}
_JSON_FIELD_NAMES = {
    "taskName": "任务名称",
    "taskTypeName": "任务类型",
    "vulnTemplate": "漏洞扫描模板",
    "reportName": "报告标题",
    "reportTitle": "报告标题",
}


def _walk_json(value: object) -> Iterable[tuple[str, object]]:
    if isinstance(value, dict):
        for key, nested in value.items():
            yield str(key), nested
            yield from _walk_json(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _walk_json(nested)


def _embedded_identity_fields(scripts: Iterable[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    decoder = json.JSONDecoder()
    for script in scripts:
        match = _WINDOW_DATA_RE.search(script)
        if match is None:
            continue
        candidate = script[match.end() :].lstrip()
        try:
            payload, _ = decoder.raw_decode(candidate)
        except json.JSONDecodeError:
            continue
        for key, value in _walk_json(payload):
            canonical = _JSON_FIELD_NAMES.get(key)
            text = _normalize(value)
            if canonical and text and canonical not in fields:
                fields[canonical] = text
    return fields


def extract_identity_fields(html_text: str) -> dict[str, str]:
    parser = _IdentityParser()
    parser.feed(html_text)
    fields: dict[str, str] = {}
    for row in parser.rows:
        for index, (tag, label) in enumerate(row[:-1]):
            normalized_label = label.rstrip("：:").strip()
            canonical = _STATIC_FIELD_NAMES.get(normalized_label)
            next_tag, value = row[index + 1]
            if canonical and tag == "th" and next_tag == "td" and value:
                fields.setdefault(canonical, value)
    for field, value in _embedded_identity_fields(parser.scripts).items():
        fields.setdefault(field, value)
    title = _normalize("".join(parser.title_parts))
    if title:
        fields.setdefault("报告标题", title)
    return fields


def classify_identity_fields(fields: dict[str, str]) -> tuple[list[str], list[dict[str, str]]]:
    evidence: list[dict[str, str]] = []

    def add(material_type: str, field: str, value: str) -> None:
        if any(
            item["材料类型"] == material_type
            and item["字段"] == field
            and item["命中内容"] == value
            for item in evidence
        ):
            return
        evidence.append({"材料类型": material_type, "字段": field, "命中内容": value})

    primary_fields = {
        field: value
        for field, value in fields.items()
        if field in {"任务名称", "任务类型", "漏洞扫描模板"} and value
    }
    # 页面标题常是扫描产品名（例如“漏洞扫描系统”），不代表当前任务类型。
    # 只有在HTML没有任务标识字段时，才用报告标题兜底。
    classification_fields = primary_fields or {
        field: value
        for field, value in fields.items()
        if field == "报告标题" and value
    }

    for field, value in classification_fields.items():
        compact = re.sub(r"\s+", "", value)
        if "弱口令" in compact:
            add("基线", field, value)
        if field == "任务类型" and "口令猜测" in compact:
            add("基线", field, value)

        if any(keyword in compact for keyword in ("漏洞", "漏扫")):
            add("漏扫", field, value)
        if field == "任务类型" and compact == "评估任务":
            add("漏扫", field, value)
        if field == "漏洞扫描模板" and compact:
            add("漏扫", field, value)

        if "渗透" in compact:
            add("渗透测试", field, value)

    matched = [
        material_type
        for material_type in MATERIAL_TYPES
        if any(item["材料类型"] == material_type for item in evidence)
    ]
    return matched, evidence


def parse_scan_archive(path: Path, *, source_name: str | None = None) -> dict[str, Any]:
    path = Path(path)
    result: dict[str, Any] = {
        "源文件": source_name or path.name,
        "状态": "解析异常",
        "入口HTML": None,
        "HTML文件数": 0,
        "HTML编码": None,
        "标识字段": {},
        "识别材料类型": [],
        "证据": [],
        "异常原因": None,
    }
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            html_infos = [
                info
                for info in infos
                if not info.is_dir()
                and info.filename.casefold().endswith((".html", ".htm"))
            ]
            result["HTML文件数"] = len(html_infos)
            entry = _preferred_html_entry(infos)
            if entry is None:
                result["状态"] = "ZIP内无HTML"
                result["异常原因"] = "ZIP压缩包中不存在HTML文件"
                return result
            result["入口HTML"] = entry.filename
            if entry.file_size > MAX_ENTRY_SIZE:
                result["状态"] = "入口HTML过大"
                result["异常原因"] = (
                    f"入口HTML解压后大小{entry.file_size}字节，超过{MAX_ENTRY_SIZE}字节上限"
                )
                return result
            if entry.flag_bits & 0x1:
                result["状态"] = "HTML无法读取"
                result["异常原因"] = "入口HTML已加密"
                return result
            html_text, encoding = _decode_html(archive.read(entry))
            fields = extract_identity_fields(html_text)
            matched_types, evidence = classify_identity_fields(fields)
            result["HTML编码"] = encoding
            result["标识字段"] = fields
            result["识别材料类型"] = matched_types
            result["证据"] = evidence
            if matched_types:
                result["状态"] = "已识别"
            else:
                result["状态"] = "类型未识别"
                result["异常原因"] = "HTML标识字段未命中基线、漏扫或渗透测试"
            return result
    except zipfile.BadZipFile:
        result["状态"] = "ZIP损坏"
        result["异常原因"] = "文件不是可读的ZIP压缩包"
    except UnicodeError as error:
        result["状态"] = "HTML编码无法识别"
        result["异常原因"] = str(error)
    except (OSError, RuntimeError, NotImplementedError) as error:
        result["状态"] = "HTML无法读取"
        result["异常原因"] = f"{type(error).__name__}: {error}"
    return result


def build_scan_report_summary(
    archives: Iterable[tuple[Path, str]],
) -> dict[str, Any]:
    results = [
        parse_scan_archive(path, source_name=source_name)
        for path, source_name in archives
    ]
    matched_types = [
        material_type
        for material_type in MATERIAL_TYPES
        if any(material_type in item["识别材料类型"] for item in results)
    ]
    type_evidence: list[dict[str, str]] = []
    for item in results:
        for evidence in item["证据"]:
            type_evidence.append(
                {
                    "源文件": item["源文件"],
                    "入口HTML": item["入口HTML"],
                    **evidence,
                }
            )
    abnormal = [
        {
            "源文件": item["源文件"],
            "状态": item["状态"],
            "异常原因": item["异常原因"],
        }
        for item in results
        if item["状态"] != "已识别"
    ]
    return {
        "schema_version": "1.0",
        "扫描报告压缩包数": len(results),
        "包含HTML压缩包数": sum(int(item["HTML文件数"] > 0) for item in results),
        "可读取HTML压缩包数": sum(
            item["状态"] in {"已识别", "类型未识别"} for item in results
        ),
        "识别材料类型": matched_types,
        "类型存在性": {
            material_type: material_type in matched_types for material_type in MATERIAL_TYPES
        },
        "类型证据": type_evidence,
        "异常压缩包": abnormal,
        "压缩包检查结果": results,
    }
