"""将 anydoc Document 渲染为带图片原位标识的 Markdown。"""

from __future__ import annotations

import mimetypes
import re
from io import BytesIO
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile


W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
WP_NS = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
VML_NS = "urn:schemas-microsoft-com:vml"
O_NS = "urn:schemas-microsoft-com:office:office"
CHART_NS = "http://schemas.openxmlformats.org/drawingml/2006/chart"
DGM_NS = "http://schemas.openxmlformats.org/drawingml/2006/diagram"
FIGURE_CAPTION_RE = re.compile(
    r"^图\s*[零〇一二三四五六七八九十百0-9]+"
    r"(?:\s*[-—－.．]\s*[0-9]+)?\s*(?:[::、.]|为)?\s*\S.*$"
)

for _prefix, _namespace in {
    "w": W_NS,
    "wp": WP_NS,
    "a": A_NS,
    "v": VML_NS,
    "o": O_NS,
    "c": CHART_NS,
    "dgm": DGM_NS,
}.items():
    ET.register_namespace(_prefix, _namespace)


@dataclass(frozen=True)
class AssetEntry:
    asset_id: int
    marker_id: str
    kind: str
    media_type: str
    origin_part: str
    file: str


@dataclass
class RenderResult:
    markdown: str
    occurrences: list[dict[str, object]]


def export_assets(document: Any, output_dir: Path, stem: str) -> dict[int, AssetEntry]:
    """导出Document资产，并返回asset_id到规范文件名的映射。"""
    if not document.assets:
        return {}

    asset_dir = output_dir / "附件" / stem
    asset_dir.mkdir(parents=True, exist_ok=True)
    image_number = 0
    object_number = 0
    result: dict[int, AssetEntry] = {}

    for asset in document.assets:
        media_type = asset.media_type.lower()
        # Word中的Visio/拓扑图常以OLE嵌入对象存储。按业务统计口径，
        # OLE也作为图片节点输出，但media_type和.bin原始附件保持不变。
        if media_type.startswith("image/") or media_type == "application/vnd.ms-ole-object":
            image_number += 1
            marker_id = f"IMG-{image_number:03d}"
            kind = "image"
        else:
            object_number += 1
            marker_id = f"OBJ-{object_number:03d}"
            kind = "object"
        filename = marker_id + _asset_extension(asset.media_type)
        target = asset_dir / filename
        target.write_bytes(asset.data)
        result[asset.id] = AssetEntry(
            asset_id=asset.id,
            marker_id=marker_id,
            kind=kind,
            media_type=asset.media_type,
            origin_part=asset.origin_part,
            file=target.relative_to(output_dir).as_posix(),
        )
    return result


def render_document(document: Any, assets: dict[int, AssetEntry]) -> RenderResult:
    renderer = _Renderer(document, assets)
    return renderer.render()


def render_anydoc_with_image_markers(
    anydoc: Any,
    docx_data: bytes,
    rendered: RenderResult,
) -> tuple[str, list[str]]:
    """借助临时alt令牌调用anydoc原生渲染器，再替换为图片标识。

    这样除图片位置外，其余Markdown由anydoc原生序列化器生成。
    """
    if not rendered.occurrences:
        return anydoc.to_markdown_bytes(docx_data, "docx"), []
    try:
        patched, replacements = _inject_tokens(docx_data, rendered.occurrences)
        markdown = anydoc.to_markdown_bytes(patched, "docx")
        for token, replacement in replacements.items():
            if token not in markdown:
                raise ValueError(f"图片令牌未出现在anydoc结果中：{token}")
            markdown = markdown.replace(token, replacement)
        return markdown, []
    except (BadZipFile, ET.ParseError, KeyError, ValueError, OSError) as error:
        return rendered.markdown, [
            f"anydoc原生Markdown图片注入失败，已使用结构渲染回退：{type(error).__name__}: {error}"
        ]


def render_pdf_with_topology_markers(
    pdf_data: bytes,
    native_markdown: str,
    output_dir: Path,
    stem: str,
) -> tuple[RenderResult, list[str]]:
    """将 PDF 中“拓扑图”所在页及紧随页渲染为可核验图片。

    anydoc 的 PDF Markdown 不保留图片对象。这里保留其原生文本/表格结果，
    只把拓扑证据页的 IMAGE 标识注入到“拓扑图”文字后。
    紧随页一并保留，兼容标题在前一页、图在后一页的排版。
    """
    try:
        import pymupdf
    except (ImportError, ModuleNotFoundError) as error:
        raise RuntimeError("PDF图片保留需要PyMuPDF，请安装requirements.txt") from error

    document = pymupdf.open(stream=pdf_data, filetype="pdf")
    topology_pages = [
        index
        for index, page in enumerate(document)
        if re.search(r"拓\s*扑\s*图", page.get_text("text"))
    ]
    if not topology_pages:
        document.close()
        return RenderResult(markdown=native_markdown, occurrences=[]), []

    evidence_pages = sorted(
        {
            page_index
            for topology_page in topology_pages
            for page_index in (topology_page, topology_page + 1)
            if page_index < len(document)
        }
    )
    asset_dir = output_dir / "附件" / stem
    asset_dir.mkdir(parents=True, exist_ok=True)
    occurrences: list[dict[str, object]] = []
    marker_lines: list[str] = []
    for number, page_index in enumerate(evidence_pages, start=1):
        marker_id = f"IMG-{number:03d}"
        filename = f"{marker_id}.png"
        target = asset_dir / filename
        page = document[page_index]
        pixmap = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
        pixmap.save(target)
        relative_file = target.relative_to(output_dir).as_posix()
        name = f"PDF第{page_index + 1}页拓扑图证据"
        record = {
            "asset_id": None,
            "marker_id": marker_id,
            "kind": "image",
            "media_type": "image/png",
            "origin_part": f"pdf/page-{page_index + 1}",
            "file": relative_file,
            "alt": name,
            "name": name,
            "location": f"pdf.page[{page_index + 1}]",
            "status": "exported",
        }
        occurrences.append(record)
        marker_lines.append(_occurrence_markup(record))
    document.close()

    markdown, inserted = _insert_after_topology_text(native_markdown, marker_lines)
    warnings = []
    if not inserted:
        markdown = native_markdown.rstrip() + "\n\n" + "\n\n".join(marker_lines) + "\n"
        warnings.append("PDF已导出拓扑证据页，但Markdown中未找到可注入的‘拓扑图’文字。")
    return RenderResult(markdown=markdown, occurrences=occurrences), warnings


def _insert_after_topology_text(markdown: str, marker_lines: list[str]) -> tuple[str, bool]:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if re.search(r"拓\s*扑\s*图", line):
            lines[index + 1 : index + 1] = ["", *marker_lines]
            return "\n".join(lines).rstrip() + "\n", True
    return markdown, False


def _inject_tokens(
    docx_data: bytes,
    occurrences: list[dict[str, object]],
) -> tuple[bytes, dict[str, str]]:
    groups = {
        "word/document.xml": [],
        "word/footnotes.xml": [],
        "word/endnotes.xml": [],
    }
    for occurrence in occurrences:
        location = str(occurrence.get("location") or "")
        if location.startswith("notes[fn"):
            groups["word/footnotes.xml"].append(occurrence)
        elif location.startswith("notes[en"):
            groups["word/endnotes.xml"].append(occurrence)
        else:
            groups["word/document.xml"].append(occurrence)

    replacements: dict[str, str] = {}
    replacement_parts: dict[str, bytes] = {}
    token_number = 0

    with ZipFile(BytesIO(docx_data)) as source:
        names = set(source.namelist())
        for part, records in groups.items():
            if not records:
                continue
            if part not in names:
                raise ValueError(f"图片记录存在，但DOCX缺少部件：{part}")
            root = ET.fromstring(source.read(part))
            targets = _image_containers(root)
            if len(targets) != len(records):
                raise ValueError(
                    f"{part}图片节点数与to_document不一致：XML={len(targets)}，模型={len(records)}"
                )
            for target, record in zip(targets, records):
                token_number += 1
                token = f"ANYDOCIMAGETOKEN{token_number:06d}END"
                doc_pr = next((node for node in target.iter() if node.tag == f"{{{WP_NS}}}docPr"), None)
                if doc_pr is None:
                    doc_pr = ET.SubElement(target, f"{{{WP_NS}}}docPr")
                doc_pr.set("descr", token)
                replacements[token] = _occurrence_markup(record)
            replacement_parts[part] = ET.tostring(root, encoding="utf-8", xml_declaration=True)

        output = BytesIO()
        with ZipFile(output, "w", compression=ZIP_DEFLATED) as target_zip:
            for info in source.infolist():
                target_zip.writestr(info, replacement_parts.get(info.filename, source.read(info.filename)))
    return output.getvalue(), replacements


def _image_containers(root: ET.Element) -> list[ET.Element]:
    container_tags = {
        f"{{{W_NS}}}drawing",
        f"{{{W_NS}}}pict",
        f"{{{W_NS}}}object",
    }
    result = []

    def walk(element: ET.Element) -> None:
        if element.tag in container_tags:
            if _is_image_container(element):
                result.append(element)
            return
        for child in element:
            walk(child)

    walk(root)
    return result


def _is_image_container(element: ET.Element) -> bool:
    tags = {node.tag for node in element.iter()}
    if f"{{{W_NS}}}txbxContent" in tags:
        return False
    if f"{{{CHART_NS}}}chart" in tags or f"{{{DGM_NS}}}relIds" in tags:
        return False
    if f"{{{O_NS}}}OLEObject" in tags:
        return True
    if f"{{{A_NS}}}blip" in tags or f"{{{VML_NS}}}imagedata" in tags:
        return True
    doc_pr = next((node for node in element.iter() if node.tag == f"{{{WP_NS}}}docPr"), None)
    return bool(doc_pr is not None and (doc_pr.attrib.get("descr") or "").strip())


def _occurrence_markup(record: dict[str, object]) -> str:
    kind = str(record.get("kind") or "image")
    marker_id = str(record.get("marker_id") or "IMG-MISSING")
    file = str(record.get("file") or "")
    alt = str(record.get("alt") or "")
    name = str(record.get("name") or record.get("caption") or alt or marker_id)
    media_type = str(record.get("media_type") or record.get("status") or "unknown")
    if kind == "object":
        return _marker("OBJECT", marker_id, file, alt, media_type, name)
    marker = _marker("IMAGE", marker_id, file, alt, media_type, name)
    if not file:
        return marker
    label = _escape_label(name)
    if str(record.get("status")) == "external":
        return f"{marker} ![{label}]({_format_url(file)})"
    return f"{marker} ![{label}](<{file}>)"


def _asset_extension(media_type: str) -> str:
    known = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/tiff": ".tiff",
        "image/bmp": ".bmp",
        "image/svg+xml": ".svg",
    }
    lowered = media_type.lower()
    if lowered in known:
        return known[lowered]
    if lowered.startswith("image/"):
        guessed = mimetypes.guess_extension(lowered)
        if guessed:
            return guessed
        subtype = re.sub(r"[^a-z0-9]", "", lowered.partition("/")[2])
        return f".{subtype}" if subtype else ".img"
    return ".bin"


class _Renderer:
    def __init__(self, document: Any, assets: dict[int, AssetEntry]) -> None:
        self.document = document
        self.assets = assets
        self.occurrences: list[dict[str, object]] = []
        self.external_number = 0
        self.unavailable_number = 0
        self.note_numbers = self._number_notes()
        self.block_captions = _top_level_figure_captions(self.document.blocks)

    def render(self) -> RenderResult:
        parts = self._render_blocks(self.document.blocks, "blocks")
        for note in sorted(
            self.document.notes,
            key=lambda item: self.note_numbers.get(item.id, 10**9),
        ):
            number = self.note_numbers.get(note.id)
            if number is None:
                continue
            body = self._render_blocks(note.blocks, f"notes[{note.id}].blocks")
            if not body:
                continue
            lines = body.splitlines()
            definition = f"[^{number}]: {lines[0] if lines else ''}"
            for line in lines[1:]:
                definition += "\n" + (f"    {line}" if line else "")
            parts.append(definition)
        markdown = "\n\n".join(part for part in parts if part)
        if markdown:
            markdown += "\n"
        return RenderResult(markdown=markdown, occurrences=self.occurrences)

    def _render_blocks(self, blocks: Iterable[Any], location: str) -> list[str]:
        rendered = []
        for index, block in enumerate(blocks):
            value = self._render_block(block, f"{location}[{index}]")
            if value:
                rendered.append(value)
        return rendered

    def _render_block(self, block: Any, location: str) -> str:
        if block.kind == "heading":
            text = self._render_inlines(block.content or [], f"{location}.heading", "heading").strip()
            level = max(1, min(6, int(block.level or 1)))
            return f"{'#' * level} {text}" if text else ""
        if block.kind == "paragraph":
            return self._render_inlines(block.content or [], f"{location}.paragraph", "block").strip()
        if block.kind == "list":
            return self._render_list(block.list, f"{location}.list")
        if block.kind == "table":
            table = block.table
            if (
                table.kind == "layout"
                and len(table.grid) == 1
                and len(table.grid[0]) == 1
                and table.grid[0][0].kind == "origin"
            ):
                return "\n\n".join(
                    self._render_blocks(table.grid[0][0].cell.blocks, f"{location}.table[0][0]")
                )
            return self._render_table(table, f"{location}.table")
        if block.kind == "block_quote":
            inner = "\n\n".join(self._render_blocks(block.blocks or [], f"{location}.quote"))
            return "\n".join(">" if not line else f"> {line}" for line in inner.splitlines())
        if block.kind == "code_block":
            text = (block.text or "").rstrip("\n")
            fence = "`" * max(3, _longest_run(text, "`") + 1)
            return f"{fence}{block.lang or ''}\n{text}\n{fence}"
        if block.kind == "rule":
            return "---"
        return ""

    def _render_inlines(self, inlines: Iterable[Any], location: str, context: str) -> str:
        result = []
        for index, inline in enumerate(inlines):
            inline_location = f"{location}.inline[{index}]"
            if inline.kind == "text":
                result.append(_styled_text(inline.text or "", inline.style, context))
            elif inline.kind == "link":
                label = self._render_inlines(inline.content or [], f"{inline_location}.link", context)
                target = inline.target.value if inline.target else ""
                if inline.target and inline.target.kind == "anchor" and target:
                    target = "#" + target.lstrip("#")
                result.append(f"[{label}]({_format_url(target)})" if target else label)
            elif inline.kind == "image":
                result.append(self._render_image(inline, inline_location, context))
            elif inline.kind == "note_ref":
                number = self.note_numbers.get(inline.note_id)
                if number is not None:
                    result.append(f"[^{number}]")
            elif inline.kind == "line_break":
                result.append("<br>" if context == "table" else (" " if context == "heading" else "  \n"))
            elif inline.kind == "checkbox":
                checked = getattr(inline, "checked", None)
                result.append("[x] " if checked is True else ("[ ] " if checked is False else ""))
            # anchor 是零宽定位信息，不在可读Markdown中输出。
        return "".join(result)

    def _render_image(self, inline: Any, location: str, context: str) -> str:
        alt = re.sub(r"\s+", " ", (inline.alt or "")).strip()
        block_match = re.match(r"^blocks\[(\d+)\]", location)
        caption = self.block_captions.get(int(block_match.group(1)), "") if block_match else ""
        source = inline.source
        if source and source.kind == "asset" and source.asset_id in self.assets:
            entry = self.assets[source.asset_id]
            name = caption or alt or entry.marker_id
            record = {
                **asdict(entry),
                "alt": alt,
                "caption": caption,
                "name": name,
                "location": location,
                "status": "exported",
            }
            self.occurrences.append(record)
            if entry.kind == "object":
                marker = _marker("OBJECT", entry.marker_id, entry.file, alt, entry.media_type, name)
                return _table_safe(marker) if context == "table" else marker
            marker = _marker("IMAGE", entry.marker_id, entry.file, alt, entry.media_type, name)
            label = _escape_label(name)
            link = f"![{label}](<{entry.file}>)"
            value = f"{marker} {link}"
            return _table_safe(value) if context == "table" else value

        if source and source.kind == "external":
            self.external_number += 1
            marker_id = f"IMG-EXT-{self.external_number:03d}"
            url = source.url or ""
            name = caption or alt or marker_id
            record = {
                "asset_id": None,
                "marker_id": marker_id,
                "kind": "image",
                "media_type": None,
                "origin_part": None,
                "file": url,
                "alt": alt,
                "caption": caption,
                "name": name,
                "location": location,
                "status": "external",
            }
            self.occurrences.append(record)
            marker = _marker("IMAGE", marker_id, url, alt, "external", name)
            value = f"{marker} ![{_escape_label(name)}]({_format_url(url)})"
            return _table_safe(value) if context == "table" else value

        self.unavailable_number += 1
        marker_id = f"IMG-MISSING-{self.unavailable_number:03d}"
        name = caption or alt or marker_id
        record = {
            "asset_id": None,
            "marker_id": marker_id,
            "kind": "image",
            "media_type": None,
            "origin_part": None,
            "file": None,
            "alt": alt,
            "caption": caption,
            "name": name,
            "location": location,
            "status": "unavailable",
        }
        self.occurrences.append(record)
        marker = _marker("IMAGE", marker_id, "", alt, "unavailable", name)
        return _table_safe(marker) if context == "table" else marker

    def _render_list(self, list_value: Any, location: str) -> str:
        if not list_value or not list_value.items:
            return ""
        items = []
        loose = False
        for index, item in enumerate(list_value.items):
            if item.marker_label:
                marker = f"- {_escape_text(item.marker_label, 'block')} "
            elif list_value.marker == "decimal":
                marker = f"{int(list_value.start) + index}. "
            else:
                marker = "- "
            # anydoc 0.2.4 将复选框从ListItem.checked迁移到了Inline.checked；
            # getattr同时兼容仍在使用旧数据模型的文档对象。
            checked = getattr(item, "checked", None)
            checkbox = "[x] " if checked is True else ("[ ] " if checked is False else "")
            body_parts = self._render_blocks(item.blocks, f"{location}.item[{index}].blocks")
            body = "\n\n".join(body_parts)
            if len(body_parts) > 1:
                loose = True
            lines = body.splitlines() or [""]
            indent = " " * len(marker)
            value = marker + checkbox + lines[0]
            for line in lines[1:]:
                if not line:
                    loose = True
                    value += "\n"
                else:
                    value += f"\n{indent}{line}"
            items.append(value)
        return ("\n\n" if loose else "\n").join(items)

    def _render_table(self, table: Any, location: str) -> str:
        if not table or not table.grid:
            return ""
        width = max((len(row) for row in table.grid), default=0)
        if width == 0:
            return ""
        rows = []
        for row_index, row in enumerate(table.grid):
            cells = []
            for col_index in range(width):
                if col_index >= len(row) or row[col_index].kind == "covered":
                    cells.append("")
                else:
                    cell = row[col_index].cell
                    cells.append(
                        self._render_cell(cell, f"{location}.row[{row_index}].col[{col_index}]")
                    )
            rows.append(cells)
        while len(rows) > 1 and not any(cell for cell in rows[-1]):
            rows.pop()
        if not any(cell for row in rows for cell in row):
            return ""
        header = rows.pop(0) if table.header_rows >= 1 else [""] * width
        lines = [_format_table_row(header), _format_table_row(["---"] * width)]
        lines.extend(_format_table_row(row) for row in rows)
        return "\n".join(lines)

    def _render_cell(self, cell: Any, location: str) -> str:
        parts = []
        for index, block in enumerate(cell.blocks):
            block_location = f"{location}.blocks[{index}]"
            if block.kind in {"paragraph", "heading"}:
                text = self._render_inlines(
                    block.content or [],
                    f"{block_location}.{block.kind}",
                    "table",
                ).strip()
                if text:
                    parts.append(f"**{text}**" if block.kind == "heading" else text)
            elif block.kind == "list":
                text = self._render_list(block.list, f"{block_location}.list").replace("\n", "<br>")
                if text:
                    parts.append(text)
            elif block.kind == "table":
                for row_index, row in enumerate(block.table.grid):
                    values = []
                    for col_index, slot in enumerate(row):
                        values.append(
                            self._render_cell(
                                slot.cell,
                                f"{block_location}.table.row[{row_index}].col[{col_index}]",
                            )
                            if slot.kind == "origin"
                            else ""
                        )
                    if any(values):
                        parts.append(" / ".join(values))
            elif block.kind == "block_quote":
                parts.extend(self._render_blocks(block.blocks or [], f"{block_location}.quote"))
            elif block.kind == "code_block" and (block.text or "").strip():
                parts.append(f"`{(block.text or '').strip().replace('`', '\\`')}`")
        return _table_safe("<br>".join(parts).strip())

    def _number_notes(self) -> dict[str, int]:
        valid = {note.id for note in self.document.notes}
        order = []

        def walk_inlines(inlines: Iterable[Any]) -> None:
            for inline in inlines:
                if inline.kind == "note_ref" and inline.note_id in valid and inline.note_id not in order:
                    order.append(inline.note_id)
                elif inline.kind == "link":
                    walk_inlines(inline.content or [])

        def walk_blocks(blocks: Iterable[Any]) -> None:
            for block in blocks:
                if block.content:
                    walk_inlines(block.content)
                if block.blocks:
                    walk_blocks(block.blocks)
                if block.list:
                    for item in block.list.items:
                        walk_blocks(item.blocks)
                if block.table:
                    for row in block.table.grid:
                        for slot in row:
                            if slot.kind == "origin":
                                walk_blocks(slot.cell.blocks)

        walk_blocks(self.document.blocks)
        for note in self.document.notes:
            if note.id not in order:
                order.append(note.id)
        return {note_id: index for index, note_id in enumerate(order, start=1)}


def _styled_text(text: str, style: Any, context: str) -> str:
    if not text:
        return ""
    if not style or text.isspace():
        return _escape_text(text, context)
    if style.code:
        fence = "`" * max(1, _longest_run(text, "`") + 1)
        return f"{fence}{text}{fence}"
    value = _escape_text(text, context)
    if style.bold and style.italic:
        value = f"***{value}***"
    elif style.bold:
        value = f"**{value}**"
    elif style.italic:
        value = f"*{value}*"
    if style.strike:
        value = f"~~{value}~~"
    return value


def _escape_text(text: str, context: str) -> str:
    text = text.replace("\\", "\\\\")
    text = re.sub(r"([`*_\[\]~])", r"\\\1", text)
    if context == "table":
        text = text.replace("|", "\\|").replace("\n", "<br>")
    return text


def _escape_label(text: str) -> str:
    return text.replace("\\", "\\\\").replace("]", "\\]").replace("[", "\\[")


def _format_url(value: str) -> str:
    if not value:
        return ""
    if any(character.isspace() for character in value) or "(" in value or ")" in value:
        return f"<{value}>"
    return value


def _marker(
    kind: str,
    marker_id: str,
    file: str,
    alt: str,
    media_type: str,
    name: str = "",
) -> str:
    values = [f"id={marker_id}"]
    if file:
        values.append(f'file="{_attribute(file)}"')
    values.append(f'name="{_attribute(name or alt or marker_id)}"')
    values.append(f'alt="{_attribute(alt)}"')
    values.append(f'type="{_attribute(media_type)}"')
    return f"[[{kind} {' '.join(values)}]]"


def _top_level_figure_captions(blocks: Iterable[Any]) -> dict[int, str]:
    """把紧邻图片的原文图题关联到图片块。

    只接受“图一 ……”、“图2-1：……”这类明确图题，避免把普通正文或
    “网络拓扑结构”章节标题误当成图名。优先使用图片后的图题，其次使用图片前的图题。
    """
    values = list(blocks)
    texts = [_plain_block_text(block) for block in values]
    result: dict[int, str] = {}
    for index, block in enumerate(values):
        if not _block_has_image(block):
            continue
        candidates = []
        if index + 1 < len(values):
            candidates.append(texts[index + 1])
        if index > 0:
            candidates.append(texts[index - 1])
        for candidate in candidates:
            normalized = re.sub(r"\s+", " ", candidate).strip()
            if len(normalized) <= 160 and FIGURE_CAPTION_RE.fullmatch(normalized):
                result[index] = normalized
                break
    return result


def _plain_block_text(block: Any) -> str:
    if getattr(block, "kind", None) not in {"paragraph", "heading"}:
        return ""
    return "".join(_plain_inline_text(inline) for inline in (getattr(block, "content", None) or []))


def _plain_inline_text(inline: Any) -> str:
    if getattr(inline, "kind", None) == "text":
        return inline.text or ""
    if getattr(inline, "kind", None) == "link":
        return "".join(_plain_inline_text(child) for child in (inline.content or []))
    if getattr(inline, "kind", None) == "line_break":
        return " "
    return ""


def _block_has_image(block: Any) -> bool:
    if getattr(block, "kind", None) not in {"paragraph", "heading"}:
        return False
    return any(getattr(inline, "kind", None) == "image" for inline in (block.content or []))


def _attribute(value: str) -> str:
    return (
        re.sub(r"\s+", " ", value)
        .strip()
        .replace('"', "&quot;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )


def _table_safe(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _format_table_row(cells: Iterable[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _longest_run(value: str, character: str) -> int:
    return max((len(match.group(0)) for match in re.finditer(re.escape(character) + "+", value)), default=0)
