from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import register_extractor


_REQUIRED_MATERIALS = (
    ("符合性评测表", "符合性评测表.md"),
    ("符合性评测报告", "符合性评测报告.md"),
)


def extract_dcz_04_data(unit_directory: str | Path) -> dict[str, Any]:
    """只检查系统目录根层的两份当前标准 Markdown。"""

    root = Path(unit_directory)
    if not root.is_dir():
        raise FileNotFoundError(f"输入系统目录不存在：{root}")

    presence: dict[str, bool] = {}
    matched_files: dict[str, str | None] = {}
    missing_materials: list[str] = []
    for material, filename in _REQUIRED_MATERIALS:
        exists = (root / filename).is_file()
        presence[material] = exists
        matched_files[material] = filename if exists else None
        if not exists:
            missing_materials.append(filename)

    return {
        "材料存在性": presence,
        "匹配文件": matched_files,
        "缺失材料": missing_materials,
    }


@register_extractor(
    "DCZ-04",
    document_name_contains=("符合性评测表.md",),
    additional_document_name_groups=(("符合性评测报告.md",),),
    input_mode="unit_directory",
)
def extract_dcz_04(source: Path) -> dict[str, Any]:
    return extract_dcz_04_data(source)
