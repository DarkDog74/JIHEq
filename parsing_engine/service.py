from __future__ import annotations

import json
import os
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from parser.emergency_material_standardizer import classify_emergency_source


EventCallback = Callable[[dict[str, object]], None]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PARSER_ROOT = PROJECT_ROOT / "parser"

CATEGORY_RULES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    ("2.6", "符合性评测表证明材料", ("符合性", "证明"), (".doc", ".docx", ".pdf")),
    ("2.7", "风险评估报告", ("风险评估",), (".doc", ".docx", ".pdf")),
    ("2.9", "符合性评测报告", ("符合性", "报告"), (".doc", ".docx")),
    ("2.1", "定级报告", ("定级报告",), (".doc", ".docx")),
    ("2.2", "备案信息表", ("备案", "信息"), (".doc", ".docx", ".wps")),
    ("2.3", "资产清单", ("资产清单",), (".xls", ".xlsx")),
    ("2.4", "三同步报告", ("三同步",), (".doc", ".docx", ".pdf", ".png", ".jpg", ".jpeg")),
    ("2.10", "演练计划", ("演练计划",), (".xls", ".xlsx")),
    ("2.5", "符合性评测表", ("符合性",), (".xls", ".xlsx")),
    ("2.8", "扫描报告", ("__scan_report_archive__",), (".zip",)),
)

_SCAN_ARCHIVE_KEYWORDS = ("扫描", "漏扫", "漏洞", "弱口令", "渗透", "基线")
_EMERGENCY_CATEGORY_BY_PURPOSE = {
    "应急预案": "emergency_plan",
    "演练方案": "drill_plan",
    "应急演练报告": "drill_report",
}
_PREVIEW_EMERGENCY_CATEGORIES = (
    ("emergency_plan", "应急预案"),
    ("drill_report", "应急演练报告"),
)


@dataclass(frozen=True)
class ParseResult:
    exit_code: int
    analysis: dict[str, object]
    output_dir: Path
    emergency_analysis: dict[str, object] = field(default_factory=dict)

    @property
    def failed_files(self) -> list[dict[str, object]]:
        files = self.analysis.get("files", [])
        standard_failures = [
            item
            for item in (files if isinstance(files, list) else [])
            if isinstance(item, dict) and item.get("status") == "failed"
        ]
        emergency_files = self.emergency_analysis.get("files", [])
        emergency_failures = [
            item
            for item in (emergency_files if isinstance(emergency_files, list) else [])
            if isinstance(item, dict) and item.get("解析状态") == "failed"
        ]
        return standard_failures + emergency_failures


def _parser_root() -> Path:
    return Path(os.environ.get("AUDIT_PARSER_ROOT", DEFAULT_PARSER_ROOT)).expanduser().resolve()


def _parser_python(root: Path) -> Path:
    configured = os.environ.get("AUDIT_PARSER_PYTHON")
    if configured:
        configured_path = Path(configured).expanduser()
        # 不可调用 resolve()：虚拟环境的 bin/python 通常是指向基础 Python
        # 的符号链接。解析该链接后再启动会丢失 pyvenv.cfg 上下文，导致
        # 子进程看不到 .venv 中已经安装的 cv2、lxml 等依赖。
        return (
            configured_path
            if configured_path.is_absolute()
            else Path.cwd() / configured_path
        )
    # 统一发布包的虚拟环境位于项目根目录，而 parser 是其子目录。
    # 直接用 `python app.py` 启动时不会注入 AUDIT_PARSER_PYTHON，
    # 仍应优先使用已安装全部解析依赖的项目虚拟环境。
    candidates = (
        root.parent / ".venv" / "bin" / "python",
        root / ".venv" / "bin" / "python",
    )
    return next((candidate for candidate in candidates if candidate.is_file()), Path(sys.executable))


def _display_name(path: Path) -> str:
    return path.name.replace("_", " ")


def _classify_preview(path: Path) -> tuple[str, str] | None:
    name = path.stem.replace(" ", "").replace("_", "").casefold()
    suffix = path.suffix.casefold()
    if suffix == ".zip":
        named_scan_archive = any(keyword in str(path) for keyword in _SCAN_ARCHIVE_KEYWORDS)
        contains_html = False
        try:
            with zipfile.ZipFile(path) as archive:
                contains_html = any(
                    not item.is_dir()
                    and item.filename.casefold().endswith((".html", ".htm"))
                    for item in archive.infolist()
                )
        except (OSError, zipfile.BadZipFile):
            pass
        if named_scan_archive or contains_html:
            return "2.8", "扫描报告"
    if (
        ("备案信息表" in name or "备案表" in name or ("备案" in name and "信息" in name))
        and suffix in {".doc", ".docx", ".wps"}
    ):
        return "2.2", "备案信息表"
    if "系统信息表" in name and suffix in {".doc", ".docx", ".wps"}:
        return "2.2", "备案信息表"
    if suffix in {".xls", ".xlsx"} and any(
        keyword in name for keyword in ("资产清单", "资产表")
    ):
        return "2.3", "资产清单"
    for code, title, keywords, suffixes in CATEGORY_RULES:
        if suffix not in suffixes:
            continue
        if code == "2.6":
            matched = ("符合性" in name and "证明" in name) or "证明材料" in name
        else:
            matched = all(keyword.casefold() in name for keyword in keywords)
        if matched:
            return code, title
    emergency_purpose = classify_emergency_source(path)
    if emergency_purpose is not None:
        return _EMERGENCY_CATEGORY_BY_PURPOSE[emergency_purpose], emergency_purpose
    return None


def _expected_categories() -> list[tuple[str, str]]:
    """返回前端需要固定展示的材料分类。"""
    ordered = sorted(CATEGORY_RULES, key=lambda rule: tuple(int(part) for part in rule[0].split(".")))
    categories: list[tuple[str, str]] = []
    for code, title, _, _ in ordered:
        categories.append((code, title))
        if code == "2.7":
            # 应急材料走独立解析流程，但仍应出现在材料预览卡片中。
            categories.extend(_PREVIEW_EMERGENCY_CATEGORIES)
    return categories


def preview_source(source_dir: Path) -> dict[str, object]:
    source = Path(source_dir).expanduser().resolve()
    if not source.is_absolute() or not source.is_dir():
        raise FileNotFoundError(f"业务系统文件夹不存在：{source}")
    recognized: list[dict[str, object]] = []
    for path in sorted(source.rglob("*"), key=lambda item: str(item)):
        if not path.is_file() or path.name.startswith("~$") or path.name.startswith("."):
            continue
        category = _classify_preview(path)
        if category is None:
            continue
        recognized.append(
            {
                "name": path.name,
                "relative_path": str(path.relative_to(source)),
                "category_code": category[0],
                "category_name": category[1],
                "size": path.stat().st_size,
            }
        )
    if not recognized:
        raise ValueError("该文件夹中没有识别到可解析的定级、备案、资产、三同步、符合性、风险评估、扫描报告、演练计划或应急演练材料")
    counts: dict[str, int] = {}
    for item in recognized:
        key = str(item["category_name"])
        counts[key] = counts.get(key, 0) + 1
    category_statuses = [
        {
            "category_code": code,
            "category_name": title,
            "count": counts.get(title, 0),
            "missing": counts.get(title, 0) == 0,
        }
        for code, title in _expected_categories()
    ]
    missing_categories = [
        str(item["category_name"])
        for item in category_statuses
        if item["missing"]
    ]
    return {
        "source_path": str(source),
        "system_name": _display_name(source),
        "department": source.parent.name,
        "recognized_count": len(recognized),
        "category_counts": counts,
        "expected_category_count": len(category_statuses),
        "present_category_count": len(category_statuses) - len(missing_categories),
        "missing_categories": missing_categories,
        "category_statuses": category_statuses,
        "documents": recognized,
    }


def run_unit_parse(
    source_dir: Path,
    output_dir: Path,
    on_event: EventCallback | None = None,
) -> ParseResult:
    source = Path(source_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    preview_source(source)
    if output == source or source in output.parents:
        raise ValueError("解析输出目录不能位于原始业务系统文件夹内")

    root = _parser_root()
    script = root / "parse_folder.py"
    python = _parser_python(root)
    if not script.is_file():
        raise FileNotFoundError(f"解析入口不存在：{script}")
    if not python.is_file():
        raise FileNotFoundError(f"解析运行环境不存在：{python}")

    output.parent.mkdir(parents=True, exist_ok=True)
    command = [str(python), str(script), str(source), "--output", str(output)]
    if on_event:
        on_event({"type": "stage", "stage": "parsing", "message": "开始解析系统材料"})
    process = subprocess.Popen(
        command,
        cwd=root,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    assert process.stdout is not None
    recent_output: list[str] = []
    for line in process.stdout:
        message = line.rstrip()
        if message:
            recent_output.append(message)
            if len(recent_output) > 40:
                recent_output.pop(0)
            if on_event:
                on_event({"type": "log", "stage": "parsing", "message": message})
    process.stdout.close()
    exit_code = process.wait()
    analysis_path = output / "文件分析.json"
    analysis: dict[str, object] = {}
    if analysis_path.is_file():
        payload = json.loads(analysis_path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            analysis = payload
    emergency_analysis_path = output / "应急材料分析.json"
    emergency_analysis: dict[str, object] = {}
    if emergency_analysis_path.is_file():
        payload = json.loads(emergency_analysis_path.read_text(encoding="utf-8-sig"))
        if isinstance(payload, dict):
            emergency_analysis = payload
    if exit_code != 0 and not analysis and not emergency_analysis:
        detail = "\n".join(recent_output[-12:]).strip()
        reason = f"单系统解析进程失败（退出码 {exit_code}），未生成文件分析结果"
        raise RuntimeError(f"{reason}：\n{detail}" if detail else reason)
    result = ParseResult(exit_code, analysis, output, emergency_analysis)
    if on_event:
        on_event(
            {
                "type": "parse_complete",
                "stage": "parsing",
                "exit_code": exit_code,
                "failed_files": len(result.failed_files),
                "message": "材料解析完成" if exit_code == 0 else "材料解析完成，部分文件存在问题",
            }
        )
    return result
