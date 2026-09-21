# -*- coding: utf-8 -*-
"""LibreOffice + OOXML based document source layer.

The production parser deliberately does not use Apache Tika.  Binary ``.doc``
files are converted to OOXML for table/text extraction and are rendered from
the original file to PDF for visual checkbox verification.  Native ``.docx``
files are parsed directly and are also rendered from the original file.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Literal

from lxml import etree

import sys
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from core.path_config import soffice_candidates


NS = {
    "w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
    "w14": "http://schemas.microsoft.com/office/word/2010/wordml",
}
W = f"{{{NS['w']}}}"

ChoiceState = Literal["checked", "unchecked", "unknown"]


@dataclass
class SymbolEvidence:
    state: ChoiceState
    raw: str
    font: str = ""
    code: str = ""
    source: str = "ooxml"
    confidence: float = 1.0
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None


@dataclass
class Cell:
    table_index: int
    row_index: int
    column_start: int
    column_span: int
    row_span: int
    raw_text: str
    normalized_text: str
    paragraphs: list[str] = field(default_factory=list)
    symbol_evidence: list[SymbolEvidence] = field(default_factory=list)
    width_twips: int = 0

    @property
    def column_end(self) -> int:
        return self.column_start + self.column_span

    def to_dict(self) -> dict:
        value = asdict(self)
        value["symbol_evidence"] = [asdict(item) for item in self.symbol_evidence]
        return value


@dataclass
class Table:
    table_index: int
    grid_columns: int
    rows: list[list[Cell]]


@dataclass
class PreparedDocument:
    source_path: Path
    structured_docx: Path
    pdf_path: Path
    source_sha256: str
    converted_from_doc: bool
    libreoffice_path: str


class DocumentPreparationError(RuntimeError):
    pass


class OoxmlParseError(RuntimeError):
    pass


def _clean_text(text: str) -> str:
    text = text.replace("\x07", "").replace("\r", "\n")
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
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
        if candidate and Path(candidate).exists():
            return str(Path(candidate))
    raise DocumentPreparationError(
        "未找到 LibreOffice/soffice；请安装 LibreOffice 或设置 LIBREOFFICE_PATH"
    )


def _run_libreoffice(
    office: str,
    source: Path,
    out_dir: Path,
    output_format: str,
    profile_dir: Path,
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        office,
        "--headless",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        output_format,
        "--outdir",
        str(out_dir),
        str(source),
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DocumentPreparationError(f"LibreOffice 转换失败: {exc}") from exc
    target = out_dir / f"{source.stem}.{output_format}"
    if completed.returncode != 0 or not target.exists():
        detail = (completed.stderr or completed.stdout or "无详细信息").strip()
        raise DocumentPreparationError(
            f"LibreOffice 未生成 {output_format}: {source.name}: {detail}"
        )
    return target


def prepare_document(doc_path: str | Path, work_dir: str | Path) -> PreparedDocument:
    source = Path(doc_path).expanduser().resolve()
    if not source.is_file():
        raise DocumentPreparationError(f"输入文件不存在: {source}")
    suffix = source.suffix.lower()
    if suffix not in {".doc", ".docx"}:
        raise DocumentPreparationError(f"仅支持 .doc/.docx: {source.name}")

    root = Path(work_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    office = resolve_libreoffice()
    if suffix == ".docx":
        structured = source
        converted = False
    else:
        structured = _run_libreoffice(
            office,
            source,
            root / "docx",
            "docx",
            root / "profile-docx",
        )
        converted = True
    pdf = _run_libreoffice(
        office,
        source,
        root / "pdf",
        "pdf",
        root / "profile-pdf",
    )
    return PreparedDocument(
        source_path=source,
        structured_docx=structured,
        pdf_path=pdf,
        source_sha256=_sha256(source),
        converted_from_doc=converted,
        libreoffice_path=office,
    )


def _font_name(run: etree._Element) -> str:
    fonts = run.find("./w:rPr/w:rFonts", namespaces=NS)
    if fonts is None:
        return ""
    for name in ("ascii", "hAnsi", "eastAsia", "cs"):
        value = fonts.get(f"{W}{name}")
        if value:
            return value
    return ""


def classify_symbol(raw: str, font: str = "", code: str = "") -> SymbolEvidence | None:
    """Classify common Word/WPS checkbox glyphs without guessing parentheses.

    ``0052`` and ``00A3`` are confirmed by the real filing-form corpus after
    LibreOffice conversion.  Plain ``R``/``£`` are only treated as markers in
    a symbol font, so product names and ordinary prose remain untouched.
    """
    f = (font or "").lower().replace(" ", "")
    h = (code or "").upper().replace("F0", "00", 1) if code else ""
    raw = raw or ""

    explicit_checked = {"☑", "☒", "✔", "✓", "√", "■", "●"}
    explicit_unchecked = {"□", "☐", "○", "◯"}
    if raw in explicit_checked:
        return SymbolEvidence("checked", raw, font, code, confidence=1.0)
    if raw in explicit_unchecked:
        return SymbolEvidence("unchecked", raw, font, code, confidence=1.0)

    checked_codes = {"0052", "F052", "0030", "F030", "00FE", "F0FE", "00FC", "F0FC"}
    unchecked_codes = {"00A3", "F0A3", "25A1", "00A8", "F0A8", "00A0", "F0A0"}
    if code:
        original = code.upper()
        if original in checked_codes or h in checked_codes:
            return SymbolEvidence("checked", raw, font, original, confidence=1.0)
        if original in unchecked_codes or h in unchecked_codes:
            return SymbolEvidence("unchecked", raw, font, original, confidence=1.0)
        return SymbolEvidence("unknown", raw, font, original, confidence=0.0)

    if raw in {"\uf052", "\uf030", "\uf0fe", "\uf0fc"}:
        return SymbolEvidence("checked", raw, font, confidence=1.0)
    if "opensymbol" in f or "wingdings" in f:
        if raw in {"R", "P", "ü", "þ"}:
            return SymbolEvidence("checked", raw, font, confidence=0.98)
        if raw in {"£"}:
            return SymbolEvidence("unchecked", raw, font, confidence=0.98)
        if raw in {"T"}:
            return SymbolEvidence("checked", raw, font, confidence=0.98)
        if raw and len(raw) == 1 and ord(raw) >= 0xE000:
            return SymbolEvidence("unknown", raw, font, confidence=0.0)
    return None


def _checkbox_state(node: etree._Element) -> ChoiceState:
    checked = node.xpath("./*[local-name()='checked']")
    default = node.xpath("./*[local-name()='default']")
    target = checked[0] if checked else (default[0] if default else None)
    if target is None:
        return "unknown"
    value = next((v for k, v in target.attrib.items() if k == "val" or k.endswith("}val")), None)
    if value is None:
        return "checked" if checked else "unchecked"
    value = value.strip().lower()
    if value in {"1", "true", "on"}:
        return "checked"
    if value in {"0", "false", "off"}:
        return "unchecked"
    return "unknown"


def _paragraph_text(paragraph: etree._Element) -> tuple[str, list[SymbolEvidence]]:
    pieces: list[str] = []
    evidence: list[SymbolEvidence] = []
    handled_checkboxes: set[int] = set()
    for node in paragraph.iter():
        local = etree.QName(node).localname
        if local in {"checkbox", "checkBox"} and id(node) not in handled_checkboxes:
            handled_checkboxes.add(id(node))
            state = _checkbox_state(node)
            marker = "☑" if state == "checked" else ("□" if state == "unchecked" else "�")
            pieces.append(marker)
            evidence.append(SymbolEvidence(state, marker, source="ooxml-control", confidence=1.0 if state != "unknown" else 0.0))
        elif local == "sym":
            font = node.get(f"{W}font", "")
            code = node.get(f"{W}char", "")
            item = classify_symbol("", font, code)
            if item:
                evidence.append(item)
                pieces.append("☑" if item.state == "checked" else ("□" if item.state == "unchecked" else "�"))
        elif local == "t":
            run = node.getparent()
            while run is not None and etree.QName(run).localname != "r":
                run = run.getparent()
            font = _font_name(run) if run is not None else ""
            text = node.text or ""
            for char in text:
                item = classify_symbol(char, font)
                if item:
                    evidence.append(item)
                    pieces.append("☑" if item.state == "checked" else ("□" if item.state == "unchecked" else "�"))
                else:
                    pieces.append(char)
        elif local == "tab":
            pieces.append("\t")
        elif local in {"br", "cr"}:
            pieces.append("\n")
    return "".join(pieces), evidence


def _cell_content(cell: etree._Element) -> tuple[str, list[str], list[SymbolEvidence]]:
    paragraphs: list[str] = []
    evidence: list[SymbolEvidence] = []
    for paragraph in cell.findall("./w:p", namespaces=NS):
        text, items = _paragraph_text(paragraph)
        paragraphs.append(_clean_text(text))
        evidence.extend(items)
    while paragraphs and paragraphs[-1] == "":
        paragraphs.pop()
    raw = "\n".join(paragraphs)
    return raw, paragraphs, evidence


def _int_val(element: etree._Element | None, attr: str = "val", default: int = 1) -> int:
    if element is None:
        return default
    raw = element.get(f"{W}{attr}") or element.get(attr)
    try:
        return int(raw) if raw is not None else default
    except ValueError:
        return default


def parse_ooxml_tables(docx_path: str | Path) -> list[Table]:
    path = Path(docx_path).resolve()
    try:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise OoxmlParseError(f"无法读取 OOXML: {path}: {exc}") from exc
    root = etree.fromstring(xml)
    table_nodes = root.xpath("//w:body//w:tbl[not(ancestor::w:tbl)]", namespaces=NS)
    tables: list[Table] = []
    for table_index, table_node in enumerate(table_nodes):
        grid_columns = len(table_node.findall("./w:tblGrid/w:gridCol", namespaces=NS))
        rows: list[list[Cell]] = []
        active_merges: dict[int, Cell] = {}
        for row_index, tr in enumerate(table_node.findall("./w:tr", namespaces=NS)):
            row: list[Cell] = []
            column = 0
            continued_columns: set[int] = set()
            for tc in tr.findall("./w:tc", namespaces=NS):
                span = _int_val(tc.find("./w:tcPr/w:gridSpan", namespaces=NS))
                merge = tc.find("./w:tcPr/w:vMerge", namespaces=NS)
                merge_value = merge.get(f"{W}val") if merge is not None else None
                if merge is not None and merge_value != "restart":
                    origin = active_merges.get(column)
                    if origin is not None:
                        origin.row_span = max(origin.row_span, row_index - origin.row_index + 1)
                        continued_columns.update(range(column, column + span))
                    column += span
                    continue

                raw, paragraphs, evidence = _cell_content(tc)
                width_twips = _int_val(
                    tc.find("./w:tcPr/w:tcW", namespaces=NS),
                    attr="w",
                    default=0,
                )
                cell = Cell(
                    table_index=table_index,
                    row_index=row_index,
                    column_start=column,
                    column_span=span,
                    row_span=1,
                    raw_text=raw,
                    normalized_text=_clean_text(raw),
                    paragraphs=paragraphs,
                    symbol_evidence=evidence,
                    width_twips=width_twips,
                )
                row.append(cell)
                for col in range(column, column + span):
                    if merge is not None and merge_value == "restart":
                        active_merges[col] = cell
                    else:
                        active_merges.pop(col, None)
                column += span
            # A merge only remains active when this physical row explicitly
            # contains a continuation cell at that column.
            for col in list(active_merges):
                origin = active_merges[col]
                if origin.row_index < row_index and col not in continued_columns:
                    active_merges.pop(col, None)
            row.sort(key=lambda item: item.column_start)
            rows.append(row)
            grid_columns = max(grid_columns, column)
        tables.append(Table(table_index=table_index, grid_columns=grid_columns, rows=rows))
    if not tables:
        raise OoxmlParseError(f"文档中没有表格: {path}")
    return tables


@contextmanager
def temporary_prepared_document(doc_path: str | Path):
    """Prepare a document in an isolated temporary directory and clean it up."""
    temporary = tempfile.TemporaryDirectory(prefix="filing-ooxml-")
    try:
        yield prepare_document(doc_path, temporary.name)
    finally:
        temporary.cleanup()
