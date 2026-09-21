"""确定性提取 Word 页眉，并生成 Markdown 首行注释。

DOCX/DOCM 直接读取 OOXML；旧版 DOC 先用 LibreOffice 转为临时 DOCX。
模块只读取源文档，不会修改源文件。
"""

from __future__ import annotations

import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
from io import BytesIO
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from core.path_config import soffice_candidates


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
W = f"{{{W_NS}}}"
R = f"{{{R_NS}}}"
PKG_REL = f"{{{PKG_REL_NS}}}"

HEADER_TYPE_LABELS = {
    "default": "默认页眉",
    "first": "首页页眉",
    "even": "偶数页眉",
}
HEADER_TYPE_ORDER = {"default": 0, "first": 1, "even": 2}


@dataclass(frozen=True)
class HeaderVariant:
    section: int
    header_type: str
    label: str
    part: str
    text: str
    fields: tuple[str, ...] = ()

    def value(self) -> str:
        if self.text:
            return self.text
        if self.fields:
            return "字段：" + "、".join(self.fields)
        return ""


@dataclass
class HeaderExtraction:
    source_format: str
    method: str
    status: str
    variants: list[HeaderVariant] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        usable = [variant for variant in self.variants if variant.value()]
        if not usable:
            return "空"

        unique_values = _deduplicate(variant.value() for variant in usable)
        if len(unique_values) == 1:
            return unique_values[0]

        counts: dict[str, int] = {}
        for variant in usable:
            counts[variant.header_type] = counts.get(variant.header_type, 0) + 1

        pieces = []
        for variant in usable:
            label = variant.label
            if counts[variant.header_type] > 1:
                label = f"第{variant.section}节{label}"
            pieces.append(f"{label}={variant.value()}")
        return "；".join(pieces)

    def markdown_comment(self) -> str:
        value = sanitize_comment(self.summary())
        return f"<!-- 页眉：{value} -->"

    def to_dict(self) -> dict[str, object]:
        return {
            "source_format": self.source_format,
            "method": self.method,
            "status": self.status,
            "summary": self.summary(),
            "variants": [asdict(variant) for variant in self.variants],
            "warnings": list(self.warnings),
        }


def sanitize_comment(value: str) -> str:
    """保证内容不会提前结束 HTML 注释，并压缩为一行。"""
    value = value.replace("--", "—")
    value = re.sub(r"[\r\n]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or "空"


def prepend_header_comment(markdown: str, extraction: HeaderExtraction) -> str:
    """将页眉注释稳定写在 Markdown 第一行。"""
    body = markdown.lstrip("\ufeff\r\n")
    return f"{extraction.markdown_comment()}\n{body}"


def extract_word_header(
    path: str | Path,
    docx_data: bytes | None = None,
) -> HeaderExtraction:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix in {".docx", ".docm"}:
        if docx_data is not None:
            return _extract_docx_bytes(docx_data, source_format=suffix[1:], method="ooxml")
        return _extract_docx(source, source_format=suffix[1:], method="ooxml")
    if suffix == ".doc":
        if docx_data is not None:
            return _extract_docx_bytes(
                docx_data,
                source_format="doc",
                method="libreoffice_then_ooxml",
            )
        return _extract_legacy_doc(source)
    return HeaderExtraction(
        source_format=suffix[1:] or "unknown",
        method="not_applicable",
        status="empty",
        warnings=["非 Word 文档，没有可提取的 Word 页眉。"],
    )


def convert_doc_to_docx_bytes(source: str | Path) -> tuple[bytes | None, list[str]]:
    """将旧版DOC转换为临时DOCX字节；不修改源文件。"""
    source = Path(source)
    soffice = _find_soffice()
    if soffice is None:
        return None, ["未找到LibreOffice，无法将旧版.doc转换为DOCX。"]

    with tempfile.TemporaryDirectory(prefix="word_header_") as temp_name:
        temp_dir = Path(temp_name)
        profile_dir = temp_dir / "lo-profile"
        output_dir = temp_dir / "converted"
        profile_dir.mkdir()
        output_dir.mkdir()
        command = [
            str(soffice),
            "--headless",
            f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
            "--convert-to",
            "docx",
            "--outdir",
            str(output_dir),
            str(source),
        ]
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, [f"LibreOffice转换失败：{type(error).__name__}: {error}"]

        converted = output_dir / f"{source.stem}.docx"
        if result.returncode != 0 or not converted.is_file():
            detail = (result.stderr or result.stdout or "未生成DOCX").strip()
            return None, [f"LibreOffice转换失败：{detail}"]
        return converted.read_bytes(), []


def _extract_legacy_doc(source: Path) -> HeaderExtraction:
    converted, warnings = convert_doc_to_docx_bytes(source)
    if converted is None:
        return HeaderExtraction(
            source_format="doc",
            method="libreoffice_then_ooxml",
            status="unavailable",
            warnings=warnings,
        )
    return _extract_docx_bytes(converted, source_format="doc", method="libreoffice_then_ooxml")


def _find_soffice() -> Path | None:
    candidates = [
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        *map(str, soffice_candidates()),
    ]
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and path.exists():
                return path
    return None


def _extract_docx(source: Path, source_format: str, method: str) -> HeaderExtraction:
    try:
        data = source.read_bytes()
    except OSError as error:
        return HeaderExtraction(
            source_format=source_format,
            method=method,
            status="error",
            warnings=[f"页眉结构读取失败：{type(error).__name__}: {error}"],
        )
    return _extract_docx_bytes(data, source_format=source_format, method=method)


def _extract_docx_bytes(data: bytes, source_format: str, method: str) -> HeaderExtraction:
    try:
        with ZipFile(BytesIO(data)) as package:
            variants = _extract_package_headers(package)
    except (BadZipFile, KeyError, ET.ParseError, OSError) as error:
        return HeaderExtraction(
            source_format=source_format,
            method=method,
            status="error",
            warnings=[f"页眉结构读取失败：{type(error).__name__}: {error}"],
        )

    status = "success" if any(item.value() for item in variants) else "empty"
    return HeaderExtraction(
        source_format=source_format,
        method=method,
        status=status,
        variants=variants,
    )


def _extract_package_headers(package: ZipFile) -> list[HeaderVariant]:
    document_part = "word/document.xml"
    document = ET.fromstring(package.read(document_part))
    relationships = _read_relationships(package, "word/_rels/document.xml.rels", document_part)
    even_and_odd = _even_and_odd_headers_enabled(package)

    sections = list(document.iter(f"{W}sectPr"))
    if not sections:
        return []

    inherited: dict[str, str] = {}
    raw_variants: list[HeaderVariant] = []

    for section_number, section in enumerate(sections, start=1):
        explicit: dict[str, str] = {}
        for reference in section.findall(f"{W}headerReference"):
            header_type = reference.attrib.get(f"{W}type", "default")
            rel_id = reference.attrib.get(f"{R}id")
            if rel_id and rel_id in relationships:
                explicit[header_type] = relationships[rel_id]

        inherited.update(explicit)
        active_types = ["default"]
        if section.find(f"{W}titlePg") is not None:
            active_types.append("first")
        if even_and_odd:
            active_types.append("even")

        for header_type in active_types:
            part = inherited.get(header_type)
            if not part or part not in package.namelist():
                continue
            text, fields = _read_header_part(package, part)
            raw_variants.append(
                HeaderVariant(
                    section=section_number,
                    header_type=header_type,
                    label=HEADER_TYPE_LABELS.get(header_type, f"{header_type}页眉"),
                    part=part,
                    text=text,
                    fields=tuple(fields),
                )
            )

    # 同一页眉被多个节继承时，只保留首次出现；不同内容则全部保留。
    seen: set[tuple[str, str, tuple[str, ...]]] = set()
    unique = []
    for variant in sorted(
        raw_variants,
        key=lambda item: (item.section, HEADER_TYPE_ORDER.get(item.header_type, 99)),
    ):
        key = (variant.header_type, variant.value(), variant.fields)
        if key in seen:
            continue
        seen.add(key)
        unique.append(variant)
    return unique


def _read_relationships(package: ZipFile, rels_part: str, base_part: str) -> dict[str, str]:
    if rels_part not in package.namelist():
        return {}
    root = ET.fromstring(package.read(rels_part))
    relationships: dict[str, str] = {}
    for relationship in root.findall(f"{PKG_REL}Relationship"):
        if relationship.attrib.get("TargetMode") == "External":
            continue
        rel_id = relationship.attrib.get("Id")
        target = relationship.attrib.get("Target")
        if not rel_id or not target:
            continue
        resolved = posixpath.normpath(posixpath.join(posixpath.dirname(base_part), target))
        relationships[rel_id] = resolved
    return relationships


def _even_and_odd_headers_enabled(package: ZipFile) -> bool:
    part = "word/settings.xml"
    if part not in package.namelist():
        return False
    settings = ET.fromstring(package.read(part))
    element = settings.find(f"{W}evenAndOddHeaders")
    if element is None:
        return False
    value = element.attrib.get(f"{W}val", "true").lower()
    return value not in {"false", "0", "off", "no"}


def _read_header_part(package: ZipFile, part: str) -> tuple[str, list[str]]:
    root = ET.fromstring(package.read(part))
    paragraphs = []
    for paragraph in root.iter(f"{W}p"):
        value = _paragraph_visible_text(paragraph)
        if value:
            paragraphs.append(value)

    fields = []
    for simple in root.iter(f"{W}fldSimple"):
        instruction = _normalize_field(simple.attrib.get(f"{W}instr", ""))
        if instruction:
            fields.append(instruction)
    fields.extend(_complex_field_instructions(root))

    return " | ".join(_deduplicate(paragraphs)), _deduplicate(fields)


def _paragraph_visible_text(paragraph: ET.Element) -> str:
    pieces = []

    def walk(element: ET.Element, is_root: bool = False) -> None:
        # 文本框中的段落会嵌套在外层段落下；内层段落会被root.iter单独处理，
        # 此处跳过可避免同一页眉文字重复两次。
        if not is_root and element.tag == f"{W}p":
            return
        if element.tag == f"{W}t" and element.text:
            pieces.append(element.text)
        elif element.tag == f"{W}tab":
            pieces.append(" ")
        elif element.tag in {f"{W}br", f"{W}cr"}:
            pieces.append(" ")
        for child in element:
            walk(child)

    walk(paragraph, is_root=True)
    text = "".join(pieces).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _normalize_field(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _complex_field_instructions(root: ET.Element) -> list[str]:
    """合并可能拆在多个 run 中的复杂字段代码。"""
    stack: list[dict[str, object]] = []
    standalone: list[str] = []
    completed: list[str] = []

    for element in root.iter():
        if element.tag == f"{W}fldChar":
            field_type = element.attrib.get(f"{W}fldCharType")
            if field_type == "begin":
                stack.append({"parts": [], "collecting": True})
            elif field_type == "separate" and stack:
                stack[-1]["collecting"] = False
            elif field_type == "end" and stack:
                frame = stack.pop()
                instruction = _normalize_field("".join(frame["parts"]))
                if instruction:
                    completed.append(instruction)
        elif element.tag == f"{W}instrText" and element.text:
            if stack and stack[-1]["collecting"]:
                stack[-1]["parts"].append(element.text)
            elif not stack:
                standalone.append(element.text)

    completed.extend(_normalize_field(value) for value in standalone)
    return [value for value in completed if value]


def _deduplicate(values: Iterable[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result
