#!/usr/bin/env python3
"""Safely repair DOCX files whose outer ZIP EOCD lost one or two zero bytes."""

from __future__ import annotations

import shutil
import struct
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path


EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_DIRECTORY_SIGNATURE = b"PK\x01\x02"
EOCD_WITHOUT_COMMENT_LENGTH = struct.Struct("<4s4H2I")
EOCD_MINIMUM_SIZE = 22
REQUIRED_DOCX_PARTS = frozenset(
    {
        "[Content_Types].xml",
        "_rels/.rels",
        "word/document.xml",
    }
)
TRUNCATED_EOCD_REPAIR_WARNING = (
    "源DOCX的ZIP结束记录缺少末尾一至两个字节，"
    "已在临时数据中补齐并完成完整性校验；原文件未修改。"
)


@dataclass(frozen=True)
class DocxRepairResult:
    data: bytes
    repaired: bool
    added_bytes: int = 0
    warning: str = ""


def _has_required_docx_parts(data: bytes) -> bool:
    """Check that the ZIP selected by its final EOCD is the outer Word package."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            return REQUIRED_DOCX_PARTS.issubset(package.namelist())
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return False


def _is_valid_repaired_docx(data: bytes) -> bool:
    """Fully verify a repaired candidate, including every member CRC/header."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as package:
            if not REQUIRED_DOCX_PARTS.issubset(package.namelist()):
                return False
            return package.testzip() is None
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile):
        return False


def _missing_empty_comment_bytes(data: bytes) -> int:
    """Return 1/2 only when the final outer EOCD is otherwise coherent."""
    if len(data) < EOCD_WITHOUT_COMMENT_LENGTH.size:
        return 0
    eocd_offset = data.rfind(EOCD_SIGNATURE)
    if eocd_offset < 0:
        return 0
    remaining = len(data) - eocd_offset
    if remaining not in {20, 21}:
        return 0
    # If one byte of the empty-comment uint16 survived, it must itself be zero.
    if any(data[eocd_offset + EOCD_WITHOUT_COMMENT_LENGTH.size :]):
        return 0
    try:
        (
            signature,
            disk_number,
            central_directory_disk,
            entries_on_disk,
            total_entries,
            central_directory_size,
            central_directory_offset,
        ) = EOCD_WITHOUT_COMMENT_LENGTH.unpack_from(data, eocd_offset)
    except struct.error:
        return 0
    if signature != EOCD_SIGNATURE:
        return 0
    if disk_number != 0 or central_directory_disk != 0:
        return 0
    if total_entries <= 0 or entries_on_disk != total_entries:
        return 0
    if central_directory_size < total_entries * 46:
        return 0
    if central_directory_offset + central_directory_size != eocd_offset:
        return 0
    if data[central_directory_offset : central_directory_offset + 4] != CENTRAL_DIRECTORY_SIGNATURE:
        return 0
    return EOCD_MINIMUM_SIZE - remaining


def repair_truncated_docx_bytes(data: bytes) -> DocxRepairResult:
    """Repair a narrowly identified truncated EOCD in memory.

    A normal DOCX is returned byte-for-byte unchanged. Unsupported corruption
    is also returned unchanged so the caller can use its existing fallback.
    """
    if _has_required_docx_parts(data):
        return DocxRepairResult(data=data, repaired=False)
    missing = _missing_empty_comment_bytes(data)
    if missing == 0:
        return DocxRepairResult(data=data, repaired=False)
    repaired = data + (b"\x00" * missing)
    if not _is_valid_repaired_docx(repaired):
        return DocxRepairResult(data=data, repaired=False)
    warning = (
        f"源DOCX的ZIP结束记录缺少末尾{missing}个字节，"
        "已在内存中补齐并完成完整性校验；原文件未修改。"
    )
    return DocxRepairResult(
        data=repaired,
        repaired=True,
        added_bytes=missing,
        warning=warning,
    )


def has_truncated_empty_zip_comment(path: str | Path) -> bool:
    """Return True only for a .docx safely repairable by the narrow rule."""
    source = Path(path)
    if source.suffix.casefold() != ".docx":
        return False
    try:
        return repair_truncated_docx_bytes(source.read_bytes()).repaired
    except OSError:
        return False


def repair_truncated_empty_zip_comment(source: str | Path, destination: str | Path) -> Path:
    """Create a fully verified repaired copy without changing the original DOCX."""
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path.suffix.casefold() != ".docx":
        raise ValueError("安全修复仅支持.docx文件")
    try:
        original = source_path.read_bytes()
    except OSError as error:
        raise ValueError(f"无法读取待修复DOCX：{error}") from error
    result = repair_truncated_docx_bytes(original)
    if not result.repaired:
        raise ValueError("文件不符合‘仅缺ZIP注释长度一至两个字节’的安全修复条件")
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination_path)
    try:
        with destination_path.open("ab") as stream:
            stream.write(result.data[len(original) :])
        if not _is_valid_repaired_docx(destination_path.read_bytes()):
            raise ValueError("补齐ZIP结束记录后，DOCX完整性或必需文件校验失败")
    except Exception:
        destination_path.unlink(missing_ok=True)
        raise
    return destination_path
