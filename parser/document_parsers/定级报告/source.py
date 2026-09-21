# -*- coding: utf-8 -*-
"""Word source preparation and ordered OOXML extraction for grading reports."""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from lxml import etree

import sys
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from core.path_config import soffice_candidates


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    "v": "urn:schemas-microsoft-com:vml",
    "pr": "http://schemas.openxmlformats.org/package/2006/relationships",
}
W = f"{{{NS['w']}}}"
R = f"{{{NS['r']}}}"


class SourceError(RuntimeError):
    pass


@dataclass
class PreparedDocument:
    source_path: Path
    structured_docx: Path
    source_sha256: str
    converted_from_doc: bool
    libreoffice_path: str = ""


@dataclass
class ImageRef:
    image_id: str
    file_name: str
    relative_path: str
    alt_text: str = ""

    def to_dict(self) -> dict:
        return {
            "图片编号": self.image_id,
            "文件名": self.file_name,
            "路径": self.relative_path,
            "替代文字": self.alt_text,
        }


@dataclass
class ParagraphPart:
    kind: str
    text: str = ""
    image: ImageRef | None = None


@dataclass
class TableCell:
    column_start: int
    column_span: int
    row_merge: str
    text: str
    row_index: int = 0
    row_span: int = 1
    paragraphs: list[str] = field(default_factory=list)
    width_twips: int = 0

    @property
    def column_end(self) -> int:
        return self.column_start + self.column_span


@dataclass
class TableImage:
    row_index: int
    column_start: int
    image: ImageRef


@dataclass
class TableData:
    table_index: int
    rows: list[list[TableCell]]
    matrix: list[list[str]]
    column_count: int
    images: list[TableImage] = field(default_factory=list)


@dataclass
class DocumentBlock:
    block_index: int
    kind: str
    text: str = ""
    style_id: str = ""
    style_name: str = ""
    images: list[ImageRef] = field(default_factory=list)
    parts: list[ParagraphPart] = field(default_factory=list)
    table: TableData | None = None


@dataclass
class DocumentStructure:
    blocks: list[DocumentBlock]
    tables: list[TableData]
    images: list[ImageRef]
    paragraph_count: int


def clean_text(text: str) -> str:
    text = (text or "").replace("\x07", "").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_libreoffice() -> str:
    candidates = [
        os.getenv("LIBREOFFICE_PATH"),
        os.getenv("SOFFICE_PATH"),
        shutil.which("soffice"),
        shutil.which("libreoffice"),
        *map(str, soffice_candidates()),
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate))
    raise SourceError("未找到 LibreOffice；.doc 文件无法转换为 DOCX")


def prepare_document(doc_path: str | Path, work_dir: str | Path) -> PreparedDocument:
    source = Path(doc_path).expanduser().resolve()
    if not source.is_file():
        raise SourceError(f"输入文件不存在: {source}")
    if source.suffix.lower() not in {".doc", ".docx"}:
        raise SourceError(f"仅支持 .doc/.docx: {source.name}")
    if source.suffix.lower() == ".docx":
        return PreparedDocument(source, source, sha256_file(source), False)

    work = Path(work_dir).resolve()
    output_dir = work / "docx"
    profile_dir = work / "lo-profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    office = resolve_libreoffice()
    command = [
        office,
        "--headless",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "docx",
        "--outdir",
        str(output_dir),
        str(source),
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SourceError(f"LibreOffice 转换失败: {exc}") from exc
    converted = output_dir / f"{source.stem}.docx"
    if result.returncode != 0 or not converted.is_file():
        detail = (result.stderr or result.stdout or "无详细信息").strip()
        raise SourceError(f"LibreOffice 未生成 DOCX: {source.name}: {detail}")
    return PreparedDocument(source, converted, sha256_file(source), True, office)


def _read_xml(package: zipfile.ZipFile, member: str) -> etree._Element:
    try:
        return etree.fromstring(package.read(member))
    except KeyError as exc:
        raise SourceError(f"DOCX 缺少必要文件: {member}") from exc
    except etree.XMLSyntaxError as exc:
        raise SourceError(f"OOXML 文件损坏: {member}: {exc}") from exc


def _style_names(package: zipfile.ZipFile) -> dict[str, str]:
    try:
        root = _read_xml(package, "word/styles.xml")
    except SourceError:
        return {}
    values: dict[str, str] = {}
    for style in root.xpath("./w:style", namespaces=NS):
        style_id = style.get(f"{W}styleId", "")
        name_node = style.find("./w:name", namespaces=NS)
        name = name_node.get(f"{W}val", "") if name_node is not None else ""
        if style_id:
            values[style_id] = name
    return values


def _relationship_targets(package: zipfile.ZipFile) -> dict[str, str]:
    root = _read_xml(package, "word/_rels/document.xml.rels")
    result: dict[str, str] = {}
    for rel in root.xpath("./pr:Relationship", namespaces=NS):
        rel_id = rel.get("Id", "")
        target = rel.get("Target", "")
        if rel_id and target and not target.startswith(("http://", "https://")):
            result[rel_id] = str(PurePosixPath("word") / target)
    return result


def _node_text(node: etree._Element) -> str:
    parts: list[str] = []
    for item in node.iter():
        local = etree.QName(item).localname
        # instrText is a Word field instruction (for example HYPERLINK/PAGEREF),
        # not visible document text. Deleted revision text is also not visible.
        if local == "t" and item.text:
            parts.append(item.text)
        elif local == "tab":
            parts.append("\t")
        elif local in {"br", "cr"}:
            parts.append("\n")
    return clean_text("".join(parts))


def _paragraph_image_ids(node: etree._Element) -> list[str]:
    ids = node.xpath(".//a:blip/@r:embed | .//v:imagedata/@r:id", namespaces=NS)
    return list(dict.fromkeys(value for value in ids if value))


def _paragraph_alt_text(node: etree._Element) -> str:
    values = node.xpath(".//wp:docPr/@descr | .//wp:docPr/@title | .//wp:docPr/@name", namespaces=NS)
    return clean_text(" / ".join(dict.fromkeys(value for value in values if value)))


def _extract_image(
    package: zipfile.ZipFile,
    rel_id: str,
    target: str,
    assets_dir: Path,
    assets_relative_dir: str,
    image_number: int,
    alt_text: str,
) -> ImageRef:
    try:
        payload = package.read(target)
    except KeyError as exc:
        raise SourceError(f"图片关系 {rel_id} 指向不存在的文件: {target}") from exc
    extension = PurePosixPath(target).suffix.lower() or ".bin"
    file_name = f"IMG{image_number:03d}{extension}"
    assets_dir.mkdir(parents=True, exist_ok=True)
    (assets_dir / file_name).write_bytes(payload)
    relative_path = str(PurePosixPath(assets_relative_dir) / file_name)
    return ImageRef(f"IMG{image_number:03d}", file_name, relative_path, alt_text)


def _paragraph_parts(
    node: etree._Element,
    package: zipfile.ZipFile,
    relationships: dict[str, str],
    image_by_relationship: dict[str, ImageRef],
    images: list[ImageRef],
    assets_dir: Path,
    assets_relative_dir: str,
    alt_text: str,
) -> list[ParagraphPart]:
    """Extract visible paragraph text and images in their OOXML order."""
    result: list[ParagraphPart] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        value = clean_text("".join(text_parts))
        text_parts.clear()
        if value:
            result.append(ParagraphPart("text", text=value))

    for item in node.iter():
        local = etree.QName(item).localname
        if local == "t" and item.text:
            text_parts.append(item.text)
        elif local == "tab":
            text_parts.append("\t")
        elif local in {"br", "cr"}:
            text_parts.append("\n")
        elif local in {"blip", "imagedata"}:
            rel_id = (
                item.get(f"{{{NS['r']}}}embed", "")
                if local == "blip"
                else item.get(f"{{{NS['r']}}}id", "")
            )
            target = relationships.get(rel_id)
            if not rel_id or not target:
                continue
            flush_text()
            if rel_id not in image_by_relationship:
                image = _extract_image(
                    package,
                    rel_id,
                    target,
                    assets_dir,
                    assets_relative_dir,
                    len(images) + 1,
                    alt_text,
                )
                image_by_relationship[rel_id] = image
                images.append(image)
            result.append(ParagraphPart("image", image=image_by_relationship[rel_id]))
    flush_text()
    return result


def _int_val(element: etree._Element | None, attr: str = "val", default: int = 1) -> int:
    if element is None:
        return default
    raw = element.get(f"{W}{attr}") or element.get(attr)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def _cell_content(cell: etree._Element) -> tuple[str, list[str]]:
    """Preserve paragraph boundaries inside one table cell."""
    paragraphs: list[str] = []
    # Match the filing-form parser: only direct cell paragraphs belong to the
    # cell value. Text boxes and nested tables are separate embedded content
    # and must not duplicate visible labels into the cell text.
    for paragraph in cell.findall("./w:p", namespaces=NS):
        paragraphs.append(_node_text(paragraph))
    while paragraphs and paragraphs[-1] == "":
        paragraphs.pop()
    return "\n".join(paragraphs), paragraphs


def _table_data(node: etree._Element, table_index: int) -> TableData:
    grid = node.xpath("./w:tblGrid/w:gridCol", namespaces=NS)
    rows: list[list[TableCell]] = []
    widest = len(grid)
    active_merges: dict[int, TableCell] = {}
    for row_index, tr in enumerate(node.xpath("./w:tr", namespaces=NS)):
        row_cells: list[TableCell] = []
        grid_before = tr.find("./w:trPr/w:gridBefore", namespaces=NS)
        column = int(grid_before.get(f"{W}val", "0")) if grid_before is not None else 0
        continued_columns: set[int] = set()
        for tc in tr.xpath("./w:tc", namespaces=NS):
            span = max(_int_val(tc.find("./w:tcPr/w:gridSpan", namespaces=NS)), 1)
            merge_node = tc.find("./w:tcPr/w:vMerge", namespaces=NS)
            merge_value = merge_node.get(f"{W}val") if merge_node is not None else None
            if merge_node is not None and merge_value != "restart":
                origin = active_merges.get(column)
                if origin is not None:
                    origin.row_span = max(origin.row_span, row_index - origin.row_index + 1)
                    continued_columns.update(range(column, column + span))
                column += span
                continue

            text, paragraphs = _cell_content(tc)
            cell = TableCell(
                column_start=column,
                column_span=span,
                row_merge="restart" if merge_node is not None else "",
                text=text,
                row_index=row_index,
                row_span=1,
                paragraphs=paragraphs,
                width_twips=_int_val(
                    tc.find("./w:tcPr/w:tcW", namespaces=NS),
                    attr="w",
                    default=0,
                ),
            )
            row_cells.append(cell)
            for occupied_column in range(column, column + span):
                if merge_node is not None and merge_value == "restart":
                    active_merges[occupied_column] = cell
                else:
                    active_merges.pop(occupied_column, None)
            column += span

        for active_column in list(active_merges):
            origin = active_merges[active_column]
            if origin.row_index < row_index and active_column not in continued_columns:
                active_merges.pop(active_column, None)
        widest = max(widest, column)
        row_cells.sort(key=lambda item: item.column_start)
        rows.append(row_cells)

    matrix: list[list[str]] = []
    for row_cells in rows:
        values = [""] * widest
        for cell in row_cells:
            if cell.column_start < widest:
                values[cell.column_start] = cell.text
        matrix.append(values)
    return TableData(table_index, rows, matrix, widest)


def _table_images(
    node: etree._Element,
    package: zipfile.ZipFile,
    relationships: dict[str, str],
    image_by_relationship: dict[str, ImageRef],
    images: list[ImageRef],
    assets_dir: Path,
    assets_relative_dir: str,
) -> list[TableImage]:
    """Extract images embedded in table cells with their logical position."""
    result: list[TableImage] = []
    for row_index, tr in enumerate(node.xpath("./w:tr", namespaces=NS)):
        grid_before = tr.find("./w:trPr/w:gridBefore", namespaces=NS)
        column = int(grid_before.get(f"{W}val", "0")) if grid_before is not None else 0
        for tc in tr.xpath("./w:tc", namespaces=NS):
            span_node = tc.find("./w:tcPr/w:gridSpan", namespaces=NS)
            try:
                span = int(span_node.get(f"{W}val", "1")) if span_node is not None else 1
            except ValueError:
                span = 1
            alt_text = _paragraph_alt_text(tc)
            for item in tc.xpath(".//a:blip | .//v:imagedata", namespaces=NS):
                local = etree.QName(item).localname
                rel_id = (
                    item.get(f"{{{NS['r']}}}embed", "")
                    if local == "blip"
                    else item.get(f"{{{NS['r']}}}id", "")
                )
                target = relationships.get(rel_id)
                if not rel_id or not target:
                    continue
                if rel_id not in image_by_relationship:
                    image = _extract_image(
                        package,
                        rel_id,
                        target,
                        assets_dir,
                        assets_relative_dir,
                        len(images) + 1,
                        alt_text,
                    )
                    image_by_relationship[rel_id] = image
                    images.append(image)
                result.append(TableImage(row_index, column, image_by_relationship[rel_id]))
            column += max(span, 1)
    return result


def extract_ooxml(
    docx_path: str | Path,
    assets_dir: str | Path,
    assets_relative_dir: str,
) -> DocumentStructure:
    """Extract paragraphs, tables and embedded images in document order."""
    source = Path(docx_path).resolve()
    asset_root = Path(assets_dir).resolve()
    with zipfile.ZipFile(source) as package:
        document = _read_xml(package, "word/document.xml")
        styles = _style_names(package)
        relationships = _relationship_targets(package)
        body = document.find("./w:body", namespaces=NS)
        if body is None:
            raise SourceError("DOCX 中未找到 word/document.xml 的正文")

        image_by_relationship: dict[str, ImageRef] = {}
        images: list[ImageRef] = []
        blocks: list[DocumentBlock] = []
        tables: list[TableData] = []
        paragraph_count = 0
        for block_index, child in enumerate(body):
            local = etree.QName(child).localname
            if local == "p":
                paragraph_count += 1
                style_node = child.find("./w:pPr/w:pStyle", namespaces=NS)
                style_id = style_node.get(f"{W}val", "") if style_node is not None else ""
                alt_text = _paragraph_alt_text(child)
                parts = _paragraph_parts(
                    child,
                    package,
                    relationships,
                    image_by_relationship,
                    images,
                    asset_root,
                    assets_relative_dir,
                    alt_text,
                )
                paragraph_images = [
                    part.image for part in parts if part.kind == "image" and part.image is not None
                ]
                blocks.append(
                    DocumentBlock(
                        block_index=block_index,
                        kind="paragraph",
                        text=_node_text(child),
                        style_id=style_id,
                        style_name=styles.get(style_id, style_id),
                        images=paragraph_images,
                        parts=parts,
                    )
                )
            elif local == "tbl":
                table = _table_data(child, len(tables))
                table.images = _table_images(
                    child,
                    package,
                    relationships,
                    image_by_relationship,
                    images,
                    asset_root,
                    assets_relative_dir,
                )
                tables.append(table)
                blocks.append(DocumentBlock(block_index, "table", table=table))
    return DocumentStructure(blocks, tables, images, paragraph_count)
