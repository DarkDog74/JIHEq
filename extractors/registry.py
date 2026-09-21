from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from .errors import MultipleRequiredDocumentsError, RequiredDocumentNotFoundError


ExtractorInput = Path | tuple[Path, ...]
ExtractorFunction = Callable[[ExtractorInput], dict[str, Any]]
ExtractorInputMode = Literal["documents", "unit_directory"]


@dataclass(frozen=True)
class ExtractorSpec:
    item_id: str
    document_name_contains: tuple[str, ...]
    additional_document_name_groups: tuple[tuple[str, ...], ...]
    file_suffixes: tuple[str, ...]
    additional_document_file_suffix_groups: tuple[tuple[str, ...], ...]
    input_mode: ExtractorInputMode
    extractor: ExtractorFunction

    @property
    def document_requirements(self) -> tuple[tuple[str, ...], ...]:
        return (self.document_name_contains, *self.additional_document_name_groups)

    @property
    def document_requirement_file_suffixes(self) -> tuple[tuple[str, ...], ...]:
        return (self.file_suffixes, *self.additional_document_file_suffix_groups)

    def matches_document(self, path: Path) -> bool:
        if self.input_mode == "unit_directory":
            return path.is_dir()
        if self.document_name_contains in {("风险评估报告",), ("风险评估报告.md",)}:
            return path.is_file() and path.name == "风险评估报告.md"
        return (
            path.is_file()
            and path.suffix.casefold() in self.file_suffixes
            and all(keyword in path.name for keyword in self.document_name_contains)
        )


_EXTRACTORS: dict[str, ExtractorSpec] = {}


def normalize_item_id(item_id: str) -> str:
    return item_id.strip().upper().replace("_", "-")


def register_extractor(
    item_id: str,
    *,
    document_name_contains: tuple[str, ...],
    additional_document_name_groups: tuple[tuple[str, ...], ...] = (),
    file_suffixes: tuple[str, ...] = (".md",),
    additional_document_file_suffix_groups: tuple[tuple[str, ...], ...] = (),
    input_mode: ExtractorInputMode = "documents",
) -> Callable[[ExtractorFunction], ExtractorFunction]:
    normalized = normalize_item_id(item_id)
    if input_mode not in ("documents", "unit_directory"):
        raise ValueError(
            f"稽核项 {normalized} 的输入模式无效：{input_mode}"
        )
    keywords = tuple(keyword.strip() for keyword in document_name_contains if keyword.strip())
    if not keywords:
        raise ValueError(f"稽核项 {normalized} 必须配置输入文档名称关键词")
    additional_groups = tuple(
        tuple(keyword.strip() for keyword in group if keyword.strip())
        for group in additional_document_name_groups
    )
    if any(not group for group in additional_groups):
        raise ValueError(f"稽核项 {normalized} 的附加文档关键词不能为空")
    def normalize_suffixes(suffixes: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            suffix.casefold() if suffix.startswith(".") else f".{suffix.casefold()}"
            for suffix in suffixes
            if suffix.strip()
        )

    normalized_suffixes = normalize_suffixes(file_suffixes)
    if not normalized_suffixes:
        raise ValueError(f"稽核项 {normalized} 必须配置输入文件扩展名")
    if additional_document_file_suffix_groups and len(
        additional_document_file_suffix_groups
    ) != len(additional_groups):
        raise ValueError(
            f"稽核项 {normalized} 的附加文档扩展名组数量必须与文档关键词组一致"
        )
    additional_suffix_groups = (
        tuple(normalize_suffixes(group) for group in additional_document_file_suffix_groups)
        if additional_document_file_suffix_groups
        else tuple(normalized_suffixes for _ in additional_groups)
    )
    if any(not group for group in additional_suffix_groups):
        raise ValueError(f"稽核项 {normalized} 的附加文档扩展名不能为空")

    def decorator(extractor: ExtractorFunction) -> ExtractorFunction:
        if normalized in _EXTRACTORS:
            raise ValueError(f"稽核项提取器重复注册：{normalized}")
        _EXTRACTORS[normalized] = ExtractorSpec(
            item_id=normalized,
            document_name_contains=keywords,
            additional_document_name_groups=additional_groups,
            file_suffixes=normalized_suffixes,
            additional_document_file_suffix_groups=additional_suffix_groups,
            input_mode=input_mode,
            extractor=extractor,
        )
        return extractor

    return decorator


def get_extractor_spec(item_id: str) -> ExtractorSpec:
    normalized = normalize_item_id(item_id)
    try:
        return _EXTRACTORS[normalized]
    except KeyError as error:
        available = "、".join(sorted(_EXTRACTORS)) or "无"
        raise KeyError(f"稽核项 {normalized} 没有提取器；可用稽核项：{available}") from error


def list_extractor_items() -> list[str]:
    return sorted(_EXTRACTORS)


def find_matching_documents(spec: ExtractorSpec, unit_dir: Path) -> list[Path]:
    return _find_matching_documents_for_keywords(
        unit_dir, spec.document_name_contains, spec.file_suffixes
    )


def _find_matching_documents_for_keywords(
    unit_dir: Path, keywords: tuple[str, ...], file_suffixes: tuple[str, ...]
) -> list[Path]:
    unit_dir = Path(unit_dir)
    if not unit_dir.is_dir():
        raise FileNotFoundError(f"输入单位目录不存在：{unit_dir}")
    markdown_files = [
        path
        for path in unit_dir.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in file_suffixes
        and path.name.casefold() != "readme.md"
    ]
    # 风险报告已按业务日期标准化。当前报告不可匹配上一份、历史或编号副本。
    if keywords in {("风险评估报告",), ("风险评估报告.md",)}:
        return sorted(path for path in markdown_files if path.name == "风险评估报告.md")
    matches = [
        path for path in markdown_files if all(keyword in path.name for keyword in keywords)
    ]
    if not matches:
        matches = [
            path
            for path in markdown_files
            if all(keyword in str(path.relative_to(unit_dir)) for keyword in keywords)
        ]
    return sorted(
        matches,
        key=lambda path: str(path.relative_to(unit_dir)),
    )


def select_input_document(spec: ExtractorSpec, unit_dir: Path) -> Path:
    unit_dir = Path(unit_dir)
    if spec.input_mode == "unit_directory":
        if not unit_dir.is_dir():
            raise FileNotFoundError(f"输入单位目录不存在：{unit_dir}")
        return unit_dir
    matches = find_matching_documents(spec, unit_dir)
    keyword_description = "、".join(spec.document_name_contains)
    if not matches:
        raise RequiredDocumentNotFoundError(
            f"单位目录中没有文件名包含“{keyword_description}”的 Markdown：{unit_dir}",
            requirement=keyword_description,
        )
    if len(matches) > 1:
        names = "、".join(str(path.relative_to(unit_dir)) for path in matches)
        raise MultipleRequiredDocumentsError(
            f"单位目录中找到多份文件名包含“{keyword_description}”的 Markdown：{names}"
        )
    return matches[0]


def select_input_documents(spec: ExtractorSpec, unit_dir: Path) -> tuple[Path, ...]:
    unit_dir = Path(unit_dir)
    if spec.input_mode == "unit_directory":
        if not unit_dir.is_dir():
            raise FileNotFoundError(f"输入单位目录不存在：{unit_dir}")
        return (unit_dir,)
    selected: list[Path] = []
    for keywords, suffixes in zip(
        spec.document_requirements, spec.document_requirement_file_suffixes, strict=True
    ):
        matches = _find_matching_documents_for_keywords(
            unit_dir, keywords, suffixes
        )
        keyword_description = "、".join(keywords)
        if not matches:
            raise RequiredDocumentNotFoundError(
                f"单位目录中没有路径或文件名包含“{keyword_description}”的目标文件：{unit_dir}",
                requirement=keyword_description,
            )
        if len(matches) > 1:
            names = "、".join(str(path.relative_to(unit_dir)) for path in matches)
            raise MultipleRequiredDocumentsError(
                f"单位目录中找到多份路径或文件名包含“{keyword_description}”的目标文件：{names}"
            )
        selected.append(matches[0])
    if len(set(selected)) != len(selected):
        raise MultipleRequiredDocumentsError("多个文档要求匹配到了同一份目标文件")
    return tuple(selected)
