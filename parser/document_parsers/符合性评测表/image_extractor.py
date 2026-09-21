from __future__ import annotations

import io
import os
import posixpath
import re
import shutil
import subprocess
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from xml.etree import ElementTree as ET

from cfb import CompoundFile, CompoundFileError

import sys
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from core.path_config import soffice_candidates


RELATIONSHIP_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
DISPIMG_RE = re.compile(r"DISPIMG.*?(ID_[A-Za-z0-9]+)", re.IGNORECASE)


class SheetLike(Protocol):
    title: str
    rows: list[list[str]]


class ImageExtractionError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawImageOccurrence:
    sheet_index: int
    row: int
    column: int
    source: str
    payload: bytes
    extension: str
    original_name: str = ""
    alt_text: str = ""


class ZipPackage:
    def __init__(self, package: zipfile.ZipFile):
        self.package = package
        self.names = package.namelist()
        self.by_lower = {name.lower(): name for name in self.names}

    def actual_name(self, name: str) -> str | None:
        return self.by_lower.get(name.lower())

    def read(self, name: str) -> bytes:
        actual = self.actual_name(name)
        if actual is None:
            raise KeyError(name)
        return self.package.read(actual)

    def find_ending(self, suffix: str) -> str | None:
        suffix_lower = suffix.lower()
        return next((name for name in self.names if name.lower().endswith(suffix_lower)), None)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _attribute_by_local_name(node: ET.Element, name: str) -> str:
    return next((value for key, value in node.attrib.items() if _local_name(key) == name), "")


def _resolve_part(source_part: str, target: str) -> str:
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _rels_part(source_part: str) -> str:
    directory = posixpath.dirname(source_part)
    name = posixpath.basename(source_part)
    return posixpath.join(directory, "_rels", f"{name}.rels")


def _relationship_map(package: ZipPackage, source_part: str) -> dict[str, tuple[str, str]]:
    rels_name = _rels_part(source_part)
    try:
        root = ET.fromstring(package.read(rels_name))
    except KeyError:
        return {}
    except ET.ParseError as exc:
        raise ImageExtractionError(f"关系文件损坏：{rels_name}: {exc}") from exc
    result: dict[str, tuple[str, str]] = {}
    for item in root:
        rel_id = item.attrib.get("Id", "")
        target = item.attrib.get("Target", "")
        target_mode = item.attrib.get("TargetMode", "")
        rel_type = item.attrib.get("Type", "")
        if rel_id and target and target_mode.lower() != "external":
            result[rel_id] = (_resolve_part(source_part, target), rel_type)
    return result


@dataclass(frozen=True)
class RegistryImage:
    payload: bytes
    extension: str
    original_name: str
    alt_text: str


def _extension_from_target(target: str) -> str:
    extension = PurePosixPath(target).suffix.lower()
    return extension if extension and len(extension) <= 10 else ".bin"


def _cell_image_registry(package: ZipPackage) -> dict[str, RegistryImage]:
    registry_part = package.find_ending("xl/cellimages.xml")
    if registry_part is None:
        return {}
    relationships = _relationship_map(package, registry_part)
    try:
        root = ET.fromstring(package.read(registry_part))
    except ET.ParseError as exc:
        raise ImageExtractionError(f"WPS 单元格图片登记表损坏：{registry_part}: {exc}") from exc

    result: dict[str, RegistryImage] = {}
    for cell_image in list(root):
        property_node = next((node for node in cell_image.iter() if _local_name(node.tag) == "cNvPr"), None)
        blip = next((node for node in cell_image.iter() if _local_name(node.tag) == "blip"), None)
        if property_node is None or blip is None:
            continue
        image_id = property_node.attrib.get("name", "")
        rel_id = _attribute_by_local_name(blip, "embed") or _attribute_by_local_name(blip, "link")
        target = relationships.get(rel_id, ("", ""))[0]
        if not image_id or not target:
            continue
        try:
            payload = package.read(target)
        except KeyError:
            continue
        alt_text = property_node.attrib.get("descr", "") or property_node.attrib.get("title", "")
        result[image_id] = RegistryImage(
            payload=payload,
            extension=_extension_from_target(target),
            original_name=PurePosixPath(target).name,
            alt_text=alt_text,
        )
    return result


def _dispimg_occurrences(package: ZipPackage, sheets: list[SheetLike]) -> list[RawImageOccurrence]:
    registry = _cell_image_registry(package)
    if not registry:
        return []
    result: list[RawImageOccurrence] = []
    for sheet_index, sheet in enumerate(sheets):
        for row_index, row in enumerate(sheet.rows):
            for col_index, value in enumerate(row):
                match = DISPIMG_RE.search(value or "")
                if not match:
                    continue
                image = registry.get(match.group(1))
                if image is None:
                    continue
                result.append(
                    RawImageOccurrence(
                        sheet_index=sheet_index,
                        row=row_index + 1,
                        column=col_index + 1,
                        source="单元格图片",
                        payload=image.payload,
                        extension=image.extension,
                        original_name=image.original_name,
                        alt_text=image.alt_text,
                    )
                )
    return result


def _workbook_sheet_parts(package: ZipPackage) -> list[str]:
    workbook_part = package.find_ending("xl/workbook.xml")
    if workbook_part is None:
        return []
    relationships = _relationship_map(package, workbook_part)
    try:
        root = ET.fromstring(package.read(workbook_part))
    except ET.ParseError as exc:
        raise ImageExtractionError(f"工作簿 XML 损坏：{exc}") from exc
    result: list[str] = []
    for node in root.iter():
        if _local_name(node.tag) != "sheet":
            continue
        rel_id = _attribute_by_local_name(node, "id")
        target = relationships.get(rel_id, ("", ""))[0]
        if target:
            result.append(target)
    return result


def _first_descendant(node: ET.Element, local_name: str) -> ET.Element | None:
    return next((item for item in node.iter() if _local_name(item.tag) == local_name), None)


def _anchor_position(anchor: ET.Element) -> tuple[int, int]:
    from_node = next((item for item in list(anchor) if _local_name(item.tag) == "from"), None)
    if from_node is None:
        return 0, 0
    row_node = next((item for item in list(from_node) if _local_name(item.tag) == "row"), None)
    col_node = next((item for item in list(from_node) if _local_name(item.tag) == "col"), None)
    try:
        row = int(row_node.text or "0") + 1 if row_node is not None else 0
        column = int(col_node.text or "0") + 1 if col_node is not None else 0
    except ValueError:
        return 0, 0
    return row, column


def _drawing_occurrences(package: ZipPackage, drawing_part: str, sheet_index: int) -> list[RawImageOccurrence]:
    relationships = _relationship_map(package, drawing_part)
    try:
        root = ET.fromstring(package.read(drawing_part))
    except (KeyError, ET.ParseError):
        return []
    result: list[RawImageOccurrence] = []
    for anchor in root.iter():
        if _local_name(anchor.tag) not in {"oneCellAnchor", "twoCellAnchor", "absoluteAnchor"}:
            continue
        row, column = _anchor_position(anchor)
        for picture in (item for item in anchor.iter() if _local_name(item.tag) == "pic"):
            blip = _first_descendant(picture, "blip")
            if blip is None:
                continue
            rel_id = _attribute_by_local_name(blip, "embed") or _attribute_by_local_name(blip, "link")
            target = relationships.get(rel_id, ("", ""))[0]
            if not target:
                continue
            try:
                payload = package.read(target)
            except KeyError:
                continue
            property_node = _first_descendant(picture, "cNvPr")
            name = property_node.attrib.get("name", "") if property_node is not None else ""
            alt_text = ""
            if property_node is not None:
                alt_text = property_node.attrib.get("descr", "") or property_node.attrib.get("title", "")
            row_hint = re.search(r"(\d+)\s*行", alt_text)
            if row_hint:
                row = int(row_hint.group(1))
            result.append(
                RawImageOccurrence(
                    sheet_index,
                    row,
                    column,
                    "浮动图片",
                    payload,
                    _extension_from_target(target),
                    name or PurePosixPath(target).name,
                    alt_text,
                )
            )
    return result


def _vml_anchor(shape: ET.Element) -> tuple[int, int]:
    anchor_node = _first_descendant(shape, "Anchor")
    if anchor_node is not None and anchor_node.text:
        parts = [item.strip() for item in anchor_node.text.split(",")]
        if len(parts) >= 8:
            try:
                start_row = int(parts[2])
                end_row = int(parts[6])
                return (start_row + end_row) // 2 + 1, int(parts[0]) + 1
            except ValueError:
                pass
    row_node = _first_descendant(shape, "Row")
    col_node = _first_descendant(shape, "Column")
    try:
        row = int(row_node.text or "0") + 1 if row_node is not None else 0
        column = int(col_node.text or "0") + 1 if col_node is not None else 0
        return row, column
    except ValueError:
        return 0, 0


def _vml_occurrences(package: ZipPackage, vml_part: str, sheet_index: int) -> list[RawImageOccurrence]:
    relationships = _relationship_map(package, vml_part)
    try:
        root = ET.fromstring(package.read(vml_part))
    except (KeyError, ET.ParseError):
        return []
    result: list[RawImageOccurrence] = []
    for shape in (node for node in root.iter() if _local_name(node.tag) == "shape"):
        image_node = _first_descendant(shape, "imagedata")
        if image_node is None:
            continue
        rel_id = _attribute_by_local_name(image_node, "id") or _attribute_by_local_name(image_node, "relid")
        target = relationships.get(rel_id, ("", ""))[0]
        if not target:
            continue
        try:
            payload = package.read(target)
        except KeyError:
            continue
        row, column = _vml_anchor(shape)
        title = _attribute_by_local_name(image_node, "title")
        name = shape.attrib.get("id", "") or title
        alt_text = shape.attrib.get("alt", "") or title
        result.append(
            RawImageOccurrence(
                sheet_index,
                row,
                column,
                "对象预览图",
                payload,
                _extension_from_target(target),
                name or PurePosixPath(target).name,
                alt_text,
            )
        )
    return result


def _package_drawing_occurrences(package: ZipPackage) -> list[RawImageOccurrence]:
    result: list[RawImageOccurrence] = []
    for sheet_index, sheet_part in enumerate(_workbook_sheet_parts(package)):
        try:
            root = ET.fromstring(package.read(sheet_part))
        except (KeyError, ET.ParseError):
            continue
        relationships = _relationship_map(package, sheet_part)
        seen_parts: set[str] = set()
        for node in root.iter():
            local = _local_name(node.tag)
            if local not in {"drawing", "legacyDrawing", "legacyDrawingHF"}:
                continue
            rel_id = _attribute_by_local_name(node, "id")
            target, rel_type = relationships.get(rel_id, ("", ""))
            if not target or target in seen_parts:
                continue
            seen_parts.add(target)
            if local == "drawing" or rel_type.endswith("/drawing"):
                result.extend(_drawing_occurrences(package, target, sheet_index))
            elif local in {"legacyDrawing", "legacyDrawingHF"} or rel_type.endswith("/vmlDrawing"):
                result.extend(_vml_occurrences(package, target, sheet_index))
    return result


def _open_package(path: Path) -> tuple[zipfile.ZipFile, ZipPackage]:
    archive = zipfile.ZipFile(path)
    return archive, ZipPackage(archive)


def _xls_cell_image_occurrences(path: Path, sheets: list[SheetLike]) -> list[RawImageOccurrence]:
    try:
        stream = CompoundFile(path).read_stream("ETCellImageData")
    except KeyError:
        return []
    except CompoundFileError as exc:
        raise ImageExtractionError(f"无法读取旧版 XLS 图片流：{path.name}: {exc}") from exc
    try:
        with zipfile.ZipFile(io.BytesIO(stream)) as archive:
            return _dispimg_occurrences(ZipPackage(archive), sheets)
    except zipfile.BadZipFile as exc:
        raise ImageExtractionError(f"XLS 中的 WPS 图片包损坏：{path.name}") from exc


def _resolve_libreoffice() -> str:
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
    raise ImageExtractionError("未找到 LibreOffice，无法提取旧版 XLS 的浮动图片")


def _convert_xls_for_drawings(path: Path, work_dir: Path) -> Path:
    output_dir = work_dir / "xlsx"
    profile_dir = work_dir / "lo-profile"
    output_dir.mkdir(parents=True, exist_ok=True)
    profile_dir.mkdir(parents=True, exist_ok=True)
    command = [
        _resolve_libreoffice(),
        "--headless",
        "--norestore",
        "--nodefault",
        "--nofirststartwizard",
        f"-env:UserInstallation={profile_dir.resolve().as_uri()}",
        "--convert-to",
        "xlsx",
        "--outdir",
        str(output_dir),
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ImageExtractionError(f"LibreOffice 转换 XLS 失败：{path.name}: {exc}") from exc
    converted = output_dir / f"{path.stem}.xlsx"
    if completed.returncode != 0 or not converted.is_file():
        detail = (completed.stderr or completed.stdout or "无详细信息").strip()
        raise ImageExtractionError(f"LibreOffice 未生成 XLSX：{path.name}: {detail}")
    return converted


def extract_image_occurrences(
    source_path: str | Path,
    sheets: list[SheetLike],
    work_dir: str | Path,
) -> list[RawImageOccurrence]:
    source = Path(source_path).resolve()
    work = Path(work_dir).resolve()
    if source.suffix.lower() == ".xlsx":
        with zipfile.ZipFile(source) as archive:
            package = ZipPackage(archive)
            result = _dispimg_occurrences(package, sheets)
            result.extend(_package_drawing_occurrences(package))
    elif source.suffix.lower() == ".xls":
        result = _xls_cell_image_occurrences(source, sheets)
        converted = _convert_xls_for_drawings(source, work)
        with zipfile.ZipFile(converted) as archive:
            result.extend(_package_drawing_occurrences(ZipPackage(archive)))
    else:
        raise ImageExtractionError(f"仅支持 .xls/.xlsx：{source.name}")
    return sorted(result, key=lambda item: (item.sheet_index, item.row or 10**9, item.column or 10**9, item.source))
