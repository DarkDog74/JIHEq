from __future__ import annotations

from pathlib import Path
from typing import Any

from .registry import register_extractor


class DCZ01ExtractionError(ValueError):
    """DCZ-01 的系统目录无法可靠检查。"""


_REQUIRED_MATERIALS = {
    "备案信息表": "备案信息表.md",
    "定级报告": "定级报告.md",
    "资产清单": "资产清单.md",
}


def extract_dcz_01_data(unit_dir: Path) -> dict[str, Any]:
    """只检查系统目录根目录下的三份固定标准 Markdown。"""

    directory = Path(unit_dir)
    if not directory.exists():
        raise FileNotFoundError(f"系统目录不存在：{directory}")
    if not directory.is_dir():
        raise NotADirectoryError(f"系统目录不是文件夹：{directory}")
    try:
        root_files = {
            path.name: path
            for path in directory.iterdir()
            if path.is_file()
        }
    except OSError as error:
        raise DCZ01ExtractionError(f"系统目录无法读取：{error}") from error

    presence: dict[str, bool] = {}
    matched_files: dict[str, list[str]] = {}
    missing: list[str] = []
    for material, file_name in _REQUIRED_MATERIALS.items():
        present = file_name in root_files
        presence[material] = present
        matched_files[material] = [file_name] if present else []
        if not present:
            missing.append(material)

    return {
        "材料存在性": presence,
        "匹配文件": matched_files,
        "缺失材料": missing,
        "必需材料数": len(_REQUIRED_MATERIALS),
        "已提供材料数": len(_REQUIRED_MATERIALS) - len(missing),
        "检查范围": "系统目录根目录下的固定标准Markdown文件",
    }


@register_extractor(
    "DCZ-01",
    document_name_contains=("备案信息表.md",),
    additional_document_name_groups=(("定级报告.md",), ("资产清单.md",)),
    input_mode="unit_directory",
)
def extract_dcz_01(source: Path | tuple[Path, ...]) -> dict[str, Any]:
    if not isinstance(source, Path):
        raise DCZ01ExtractionError("DCZ-01需要一个系统目录作为输入")
    return extract_dcz_01_data(source)
