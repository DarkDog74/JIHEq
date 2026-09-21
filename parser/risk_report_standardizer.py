"""按检验结束日期给已解析的风险报告分配当前、上一份和历史角色。

仅移动 Markdown，附件目录与 Markdown 内相对引用保持不变。
"""
from __future__ import annotations

import sys
import re
from pathlib import Path
from tempfile import TemporaryDirectory, mkdtemp
from typing import Any

# parse_folder.py 既可作为脚本运行，也可被测试导入。
if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from extractors.fx_02 import FX02ExtractionError, extract_inspection_end_date


def backup_risk_outputs(output_dir: Path) -> None:
    """类别重跑时将旧风险输出移到系统目录外，防止历史文件残留参与稽核。"""
    pattern = re.compile(r"^(?:风险评估报告(?:（\d+）)?|上一份风险评估报告|"
                         r"历史风险评估报告_\d{4}-\d{2}-\d{2}|待确认风险评估报告_\d+)$")
    paths = [p for p in output_dir.iterdir() if p.suffix == ".md" and pattern.fullmatch(p.stem)]
    assets = output_dir / "附件"
    if assets.is_symlink():
        raise ValueError(f"拒绝操作符号链接附件目录：{assets}")
    if assets.is_dir():
        paths += [p for p in assets.iterdir() if pattern.fullmatch(p.name)]
    if any(p.is_symlink() for p in paths):
        raise ValueError("旧风险报告输出中存在符号链接")
    if not paths:
        return
    backup = Path(mkdtemp(prefix="risk-report-output-backup-"))
    for path in paths:
        target = backup / path.relative_to(output_dir)
        target.parent.mkdir(parents=True, exist_ok=True)
        path.rename(target)
    print(f"旧风险报告输出已备份：{backup}")


def standardize_risk_reports(output_dir: Path, analysis: dict[str, Any]) -> bool:
    records = [r for r in analysis["files"] if r.get("category_code") == "2.7"
               and r.get("status") in {"success", "failed"} and r.get("output")]
    dates: dict[str, str] = {}
    errors: list[str] = []
    for record in records:
        path = output_dir / record["output"]
        if path.parent != output_dir or path.is_symlink() or not path.is_file():
            raise ValueError(f"风险报告输出路径无效：{path}")
        try:
            if record["status"] != "success":
                raise FX02ExtractionError("报告解析失败")
            dates[record["output"]] = extract_inspection_end_date(
                path.read_text(encoding="utf-8-sig")
            ).isoformat()
        except FX02ExtractionError as error:
            errors.append(f"{record['source']}：{error}")
        record["inspection_end_date"] = dates.get(record["output"])

    # 单份报告无需推断先后；保留原有其他稽核能力，FX-02 仍自行检查日期。
    ambiguous = len(records) > 1 and bool(errors)
    if len(set(dates.values())) != len(dates):
        ambiguous = True
        errors.append("存在相同检验结束日期，不能将同次评估副本认定为两次评估")
    ordered = sorted(records, key=lambda r: (dates.get(r["output"], ""), r["source"]), reverse=True)
    moves: list[tuple[Path, Path, dict[str, Any], str]] = []
    for rank, record in enumerate(ordered, 1):
        if ambiguous:
            stem, role = f"待确认风险评估报告_{rank}", "待确认"
        elif rank == 1:
            stem, role = "风险评估报告", "最新"
        elif rank == 2:
            stem, role = "上一份风险评估报告", "上一份"
        else:
            stem, role = f"历史风险评估报告_{dates[record['output']]}", "历史"
        moves.append((output_dir / record["output"], output_dir / f"{stem}.md", record, role))

    sources = {old for old, _, _, _ in moves}
    for _, new, _, _ in moves:
        if new not in sources and new.exists():
            raise FileExistsError(f"拒绝覆盖未属于本轮解析的文件：{new}")
    # 两阶段移动，避免新旧文件互换时覆盖；失败恢复原始命名。
    with TemporaryDirectory(prefix=".risk-ranking-", dir=output_dir) as tmp:
        staged: list[tuple[Path, Path, Path]] = []
        completed: list[tuple[Path, Path, Path]] = []
        try:
            for index, (old, new, _, _) in enumerate(moves):
                saved = Path(tmp) / f"{index}.md"
                old.rename(saved)
                staged.append((old, new, saved))
            for entry in staged:
                entry[2].rename(entry[1])
                completed.append(entry)
        except Exception:
            for _, new, saved in completed:
                new.rename(saved)
            for old, _, saved in staged:
                saved.rename(old)
            raise
    for _, new, record, role in moves:
        record["output"] = new.name
        record["risk_report_role"] = role
        if ambiguous:
            record.setdefault("warnings", []).append("风险报告新旧排序失败：" + "；".join(errors))
    analysis["risk_report_ordering"] = {
        "status": "ambiguous" if ambiguous else "success",
        "errors": errors,
        "reports": [{"source": r["source"], "output": r["output"],
                     "inspection_end_date": r["inspection_end_date"],
                     "role": r["risk_report_role"]} for r in ordered],
    }
    if ambiguous:
        print("风险报告排序失败，已保留为待确认文件：" + "；".join(errors))
    return not ambiguous
