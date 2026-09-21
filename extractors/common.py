"""模型稽核项提取器的共用工具。

提取器保持自包含叶子包（不依赖 pipeline / platform_app），因此
audits 落盘、IMAGE 引用解析、章节定位等能力在包内自行实现。
"""

from __future__ import annotations

import json
import re
import shutil
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

_IMAGE_BLOCK_RE = re.compile(r"\[\[IMAGE\b(?P<attrs>[^\]]*)\]\]")
_IMAGE_ATTR_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^\s\]]+)"
)
_HEADING_LINE_RE = re.compile(r"^(?P<marks>#{1,6})\s+(?P<title>.+?)\s*$")
_LEADING_NUMBER_RE = re.compile(r"^\d+(?:\s*[.．、]\s*\d+)*\s*[.．、]?\s*")
_ROOT_IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png"})
_ARTIFACT_DIR: ContextVar[Path | None] = ContextVar("audit_artifact_dir", default=None)


@contextmanager
def audit_artifact_scope(item_dir: Path) -> Iterator[None]:
    """把图片附件定向到本次输出目录，禁止提取器反向写入输入材料目录。"""
    token = _ARTIFACT_DIR.set(Path(item_dir))
    try:
        yield
    finally:
        _ARTIFACT_DIR.reset(token)


def portable_artifact_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
    """移除附件运行时绝对路径，落盘结果只保留可迁移相对路径。"""
    payload = deepcopy(dict(fields))
    images = payload.get("图片文件")
    if isinstance(images, list):
        for entry in images:
            if not isinstance(entry, dict):
                continue
            if entry.get("相对路径"):
                entry.pop("路径", None)
    return payload


def resolve_system_root(source: Path) -> Path:
    """从输入文档路径回溯系统根目录（parsed/ 的上一级）。"""
    source = Path(source)
    if source.is_dir():
        current = source
    else:
        current = source.parent
    while True:
        if current.name == "parsed":
            return current.parent
        if current.parent == current:
            break
        current = current.parent
    # 兜底：常规产物位于 <系统根>/parsed/ 下，回溯两级。
    return current if source.is_dir() else source.parent.parent


def parse_markdown_heading(line: str) -> tuple[int, str] | None:
    """解析 `#` 前缀标题，返回 (层级, 去装饰标题)；非标题行返回 None。"""
    match = _HEADING_LINE_RE.match(line.strip())
    if match is None:
        return None
    title = match.group("title").replace("**", "").replace("__", "").strip()
    return len(match.group("marks")), title


def normalize_heading_title(title: str) -> str:
    """去掉编号与空白，用于标题语义比较。"""
    collapsed = re.sub(r"\s+", "", title.replace("**", "").replace("__", ""))
    return _LEADING_NUMBER_RE.sub("", collapsed)


def slice_section_by_heading(
    lines: list[str], start_index: int, level: int
) -> list[str]:
    """取标题行之后、直到下一个同级或更高级标题前的内容行。"""
    end = len(lines)
    for index in range(start_index + 1, len(lines)):
        heading = parse_markdown_heading(lines[index])
        if heading is not None and heading[0] <= level:
            end = index
            break
    return lines[start_index + 1 : end]


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def parse_image_refs(markdown: str) -> list[dict[str, Any]]:
    """解析全文 `[[IMAGE id=... file="..." ...]]` 引用，带行号。"""
    refs: list[dict[str, Any]] = []
    for line_number, line in enumerate(markdown.splitlines(), start=1):
        for block in _IMAGE_BLOCK_RE.finditer(line):
            attrs = {
                match.group("key"): _strip_quotes(match.group("value"))
                for match in _IMAGE_ATTR_RE.finditer(block.group("attrs"))
            }
            refs.append(
                {
                    "行号": line_number,
                    "id": attrs.get("id", ""),
                    "file": attrs.get("file", ""),
                    "name": attrs.get("name", ""),
                    "alt": attrs.get("alt", ""),
                    "type": attrs.get("type", ""),
                }
            )
    return refs


def resolve_image_path(ref: Mapping[str, Any], base_dir: Path) -> Path:
    """IMAGE 引用的 file 属性按 parsed/ 目录解析为绝对路径。"""
    file_value = str(ref.get("file") or "").strip()
    if not file_value:
        raise ValueError("IMAGE 引用缺少 file 属性")
    candidate = Path(file_value)
    if not candidate.is_absolute():
        candidate = Path(base_dir) / candidate
    return candidate


def collect_root_images(system_root: Path) -> list[Path]:
    """系统根目录下直接存放的 jpg/png（非递归，与 parsed/ 平级）。"""
    system_root = Path(system_root)
    if not system_root.is_dir():
        return []
    return sorted(
        (
            path
            for path in system_root.iterdir()
            if path.is_file() and path.suffix.casefold() in _ROOT_IMAGE_SUFFIXES
        ),
        key=lambda path: path.name,
    )


def clean_inline_value(value: Any) -> str:
    """压平备案信息等字段的换行与多余空白。"""
    return re.sub(r"\s+", "", str(value)) if value is not None else ""


def find_beian_field(beian: Mapping[str, Any], needle: str) -> str | None:
    """按关键字模糊匹配备案信息表顶层键（键名含“/”“*”等装饰）。"""
    for key, value in beian.items():
        if needle in str(key):
            cleaned = clean_inline_value(value)
            return cleaned or None
    return None


def _portable_path(path: Path, system_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(system_root.resolve()))
    except ValueError:
        return str(path)


def _unique_name(source: Path, used: set[str]) -> str:
    name = source.name
    if name not in used:
        return name
    # 同名不同源：加父目录前缀；仍冲突则加序号。
    name = f"{source.parent.name}_{source.name}"
    counter = 2
    while name in used:
        name = f"{source.stem}_{counter}{source.suffix}"
        counter += 1
    return name


def stage_audit_artifacts(
    item_id: str,
    sources: Path | tuple[Path, ...] | list[Path],
    fields: dict[str, Any],
    images: Iterable[Mapping[str, Any]] = (),
) -> Path:
    """把提取结果与原始输入图片落盘到 <系统根>/audits/<编号>/。

    images 中每项需含 “路径”（源图片绝对路径），其余键作为说明保留。
    成功落盘后会把 `图片文件`（audits 副本绝对路径列表）写回 fields，
    供规则层直接读取文件调用 OCR / 视觉模型。
    """
    source_paths = [
        Path(source)
        for source in (sources if isinstance(sources, (tuple, list)) else (sources,))
    ]
    if not source_paths:
        raise ValueError("stage_audit_artifacts 需要至少一个输入文档")

    system_root = resolve_system_root(source_paths[0])
    item_dir = _ARTIFACT_DIR.get() or (system_root / "audits" / item_id)
    item_dir.mkdir(parents=True, exist_ok=True)
    if "audits" in item_dir.parts:
        audits_index = item_dir.parts.index("audits")
        relative_prefix = "/".join(item_dir.parts[audits_index:])
    else:
        relative_prefix = "."

    staged_images: list[dict[str, Any]] = []
    staged_field_files: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for image in images:
        entry = {key: value for key, value in image.items() if key != "路径"}
        source = Path(image["路径"])
        if not source.is_file():
            staged_images.append(
                {**entry, "原始路径": _portable_path(source, system_root), "复制状态": "源文件缺失"}
            )
            staged_field_files.append({**entry, "路径": str(source), "复制状态": "源文件缺失"})
            continue
        name = _unique_name(source, used_names)
        used_names.add(name)
        shutil.copy2(source, item_dir / name)
        staged_images.append(
            {
                **entry,
                "原始路径": _portable_path(source, system_root),
                "audits内文件": name,
                "复制状态": "已复制",
            }
        )
        staged_field_files.append(
            {
                **entry,
                "路径": str(item_dir / name),
                "相对路径": f"{relative_prefix}/{name}" if relative_prefix != "." else name,
            }
        )

    if staged_field_files:
        fields["图片文件"] = staged_field_files

    payload: dict[str, Any] = {
        **dict(fields),
        "输入文档": [_portable_path(path, system_root) for path in source_paths],
    }
    if staged_images:
        payload["原始输入图片"] = staged_images

    fields_file = item_dir / f"{item_id}.json"
    tmp_file = fields_file.with_name(f"{item_id}.json.tmp")
    tmp_file.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tmp_file.replace(fields_file)
    return item_dir
