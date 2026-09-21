#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐部门(或整批)解析结果核验器:源文件全文本 ↔ 业务JSON 归一化覆盖比对。

方法论沿用《解析结果审核报告.md》:
  1. 从 manifest.json 读取每个逻辑文档的「采用源文件」与「主JSON」;
  2. 用与解析器独立的通道提取源文件全部文本(Word: LibreOffice→docx→python-docx
     段落+表格单元格(合并格去重); Excel: openpyxl/xlrd 全单元格; PDF: pdfplumber);
  3. 把 JSON 里全部字符串键值拍平成文本集合;
  4. 归一化(去空白/全半角/大小写)后计算「源条目被JSON覆盖」比例,输出未覆盖清单。

判定口径: 覆盖率 100% = ✅;≥97% 且未覆盖项均为模板行/编号/短符号 = ✅(带说明);
其余分级 ⚠️/❌,留待人工逐段复核。

用法:
    python verify_dept.py <manifest.json> [--out 报告.md] [--min-coverage 0.97] [--max-items 40]
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from core.path_config import soffice_candidates

# ---------- LibreOffice ----------

SOFFICE_CANDIDATES = [
    *map(str, soffice_candidates()),
]


def _soffice() -> str:
    import shutil

    for candidate in SOFFICE_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    found = shutil.which("soffice")
    if found:
        return found
    raise RuntimeError("未找到 soffice")


def _doc_to_docx(source: Path, work_dir: Path) -> Path:
    target = work_dir / (source.stem + ".docx")
    subprocess.run(
        [_soffice(), "--headless", "--norestore", "--convert-to", "docx",
         "--outdir", str(work_dir), str(source)],
        check=True, capture_output=True, timeout=300,
    )
    if not target.is_file():
        raise RuntimeError(f"LibreOffice 未产出 {target.name}")
    return target


# ---------- 源文本提取 ----------

def _iter_docx_texts(docx_path: Path):
    import docx
    from docx.document import Document as _Doc
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn

    document = docx.Document(str(docx_path))

    def iter_block_items(parent):
        body = parent.element.body if isinstance(parent, _Doc) else parent._element
        for child in body.iterchildren():
            if child.tag == qn("w:p"):
                yield Paragraph(child, parent)
            elif child.tag == qn("w:tbl"):
                yield Table(child, parent)

    # 顺序遍历段落与表格,合并单元格按「同值只取一次」去重
    for block in iter_block_items(document):
        if isinstance(block, Paragraph):
            text = block.text.strip()
            if text:
                yield text
        else:
            seen_cells: set[str] = set()
            for row in block.rows:
                for cell in row.cells:
                    text = cell.text.strip()
                    if text and text not in seen_cells:
                        seen_cells.add(text)
                        yield text
    # 页眉页脚
    for section in document.sections:
        for part in (section.header, section.footer):
            for paragraph in part.paragraphs:
                text = paragraph.text.strip()
                if text:
                    yield text
            for table in part.tables:
                seen_cells = set()
                for row in table.rows:
                    for cell in row.cells:
                        text = cell.text.strip()
                        if text and text not in seen_cells:
                            seen_cells.add(text)
                            yield text


def source_texts(source: Path, work_dir: Path):
    suffix = source.suffix.lower()
    if suffix in {".doc", ".docx"}:
        docx_path = source if suffix == ".docx" else _doc_to_docx(source, work_dir)
        yield from _iter_docx_texts(docx_path)
    elif suffix in {".xls", ".xlsx"}:
        if suffix == ".xlsx":
            import openpyxl
            book = openpyxl.load_workbook(str(source), data_only=True, read_only=True)
            for sheet in book.worksheets:
                for row in sheet.iter_rows(values_only=True):
                    for value in row:
                        if value is None:
                            continue
                        text = str(value).strip()
                        if text:
                            yield text
        else:
            import xlrd
            book = xlrd.open_workbook(str(source))
            for sheet in book.sheets():
                for row_index in range(sheet.nrows):
                    for col_index in range(sheet.ncols):
                        value = sheet.cell_value(row_index, col_index)
                        if value is None:
                            continue
                        text = str(value).strip()
                        if text:
                            yield text
    elif suffix == ".pdf":
        import pdfplumber
        with pdfplumber.open(str(source)) as pdf:
            for page in pdf.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    for line in text.splitlines():
                        line = line.strip()
                        if line:
                            yield line
    else:
        raise RuntimeError(f"不支持的源格式 {suffix}")


# ---------- JSON 文本拍平 ----------

def _flatten(value, out: list[str]):
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key).strip()
            if key_text and key_text not in {"图片", "前置内容", "文档名称"}:
                out.append(key_text)
            _flatten(item, out)
    elif isinstance(value, list):
        for item in value:
            _flatten(item, out)
    elif isinstance(value, str):
        text = value.strip()
        if text:
            out.append(text)
    elif isinstance(value, (int, float)):
        out.append(str(value))


def json_texts(data) -> list[str]:
    out: list[str] = []
    _flatten(data, out)
    return out


# ---------- 归一化与比对 ----------

_SYMBOLS = re.compile(r"[\s　·•\-—_=*#①②③④⑤⑥⑦⑧⑨⑩□☑☒✓√×xX/\\|,，。.、;；:：()（）\[\]【】<>《》“”\"'‘’!?！？]+")


# 解析器把行业缩写展开为全称(如 CDN→互联网内容分发网络),比对前做同义归一
_SYNONYMS = (
    ("cdn", "互联网内容分发网络"),
    ("wlan", "无线局域网"),
)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    # Wingdings 私用区勾选字形与常见变体,统一为 ☑/□
    text = text.replace("", "☑").replace("", "☒")
    text = text.replace("", "☑").replace("", "☑")
    text = text.replace("£", "□")
    text = text.replace("", "☑")
    text = text.lower()
    for short, full in _SYNONYMS:
        text = text.replace(short, full)
    return _SYMBOLS.sub("", text)


def _json_blob(json_values: list[str]) -> str:
    return "\n".join(normalize(v) for v in json_values if normalize(v))


# 口径内白名单: 解析器设计排除或非业务内容的源行
_EXCLUDE_PATTERNS = (
    r"^网络单元备案信息表$",                        # 备案表大标题(设计排除)
    r"DISPIMG",                                     # WPS 嵌入图片公式
    r"^修订历史(\s*REVISION HISTORY)?$",           # 定级报告表头
    r"^文档名称$",
    r"^(版本号|修订日期|作者|日期|版本|页码|密级)$",   # 通用表头单元格
    r"^共\s*\d+\s*页|^第\s*\d+\s*页",
    r"^填表人[:：]?|^填表日期[:：]?|^填表时间[:：]?",
    r"^备注[:：]?|^注[:：]?\s*$",
    r"^\d{4}年\d{1,2}月\d{1,2}日$",
)


def _is_excluded(item: str) -> bool:
    return any(re.search(p, item, re.IGNORECASE) for p in _EXCLUDE_PATTERNS)


def _checkbox_options(item: str) -> list[str]:
    """把勾选组行拆成选项词;非勾选行返回空。

    拆分用 □/换行 + 「跨 N 个」「：」等连接噪声,避免相邻选项粘连
    (如 "□跨省… 跨 个\n全省…" 被并为一个词导致漏配)。
    """
    if not re.search(r"[□☑☒✓√£\uf0a8\uf0fe\uf0d8\uf06f\uf052]", item):
        return []
    parts = re.split(r"[□☑☒✓√£\uf0a8\uf0fe\uf0d8\uf06f\uf052\n]+", item)
    options = []
    for part in parts:
        norm = normalize(part)
        if not norm or norm in {"其他", "其它"}:  # 空"其他（）"非选中不校验
            continue
        # 「跨 个」是选项组固有连接词,不是选项内容
        for word in re.split(r"跨\d*个|：|:", norm):
            if word:
                options.append(word)
    return options


def _line_covered(item: str, norm: str, blob: str) -> bool:
    if norm in blob:
        return True
    # 勾选组行: 只要任一选项词(即被选项)出现在 JSON 即覆盖
    options = _checkbox_options(item)
    if options and any(opt in blob for opt in options):
        return True
    return False


def coverage(source_items: list[str], blob: str):
    total = len(source_items)
    covered, uncovered = 0, []
    for item in source_items:
        norm = normalize(item)
        if not norm:
            covered += 1
            continue
        if _is_excluded(item):
            covered += 1
            continue
        if _line_covered(item, norm, blob):
            covered += 1
        else:
            uncovered.append(item)
    return covered, total, uncovered


# ---------- 主流程 ----------

def classify_uncovered(uncovered: list[str]) -> tuple[list[str], list[str]]:
    """把未覆盖项粗分为『口径内可解释』与『需人工复核』。"""
    explained, manual = [], []
    patterns = (
        r"^[\d\.\-]+$",                 # 纯编号
        r"^填表人", r"^填表日期", r"^填表时间",
        r"^备注", r"^注[:：]?", r"^说明[:：]?",
        r"^第\s*\d+\s*页.*页?$", r"^共\s*\d+\s*页",
        r"^\d{4}年\d{1,2}月\d{1,2}日$",
        r"^□+$", r"^☑+$",
    )
    for item in uncovered:
        if any(re.search(p, item) for p in patterns) or len(normalize(item)) <= 2:
            explained.append(item)
        else:
            manual.append(item)
    return explained, manual


def verify(manifest_path: Path, min_coverage: float, max_items: int) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = []
    with tempfile.TemporaryDirectory(prefix="verify-dept-") as work:
        work_dir = Path(work)
        for entry in manifest.get("文档", []):
            category = entry["类别"]
            source = Path(entry["采用源文件"])
            main_json = Path(entry["主JSON"])
            status = entry.get("状态", "?")
            record = {
                "类别": category,
                "逻辑文档键": entry.get("逻辑文档键", ""),
                "源文件": str(source),
                "主JSON": str(main_json),
                "状态": status,
            }
            if status == "error":
                record.update(判定="❌", 覆盖率=None, 说明="解析error,无主JSON")
                results.append(record)
                continue
            if not main_json.is_file():
                record.update(判定="❌", 覆盖率=None, 说明="主JSON缺失")
                results.append(record)
                continue
            try:
                items = [t for t in source_texts(source, work_dir)]
                blob = _json_blob(json_texts(json.loads(main_json.read_text(encoding="utf-8"))))
                covered, total, uncovered = coverage(items, blob)
                rate = covered / total if total else 1.0
                explained, manual = classify_uncovered(uncovered)
                core_rate = (covered + len(explained)) / total if total else 1.0
                if rate >= 1.0:
                    verdict = "✅"
                elif core_rate >= min_coverage and len(manual) <= 3:
                    verdict = "✅(带说明)"
                elif core_rate >= 0.90:
                    verdict = "⚠️"
                else:
                    verdict = "❌"
                record.update(
                    判定=verdict,
                    覆盖率=round(rate * 100, 1),
                    口径内覆盖率=round(core_rate * 100, 1),
                    源条目=total,
                    未覆盖=len(uncovered),
                    需人工=len(manual),
                    未覆盖样例=manual[:max_items],
                    口径内排除样例=explained[:10],
                )
            except Exception as exc:
                record.update(判定="❌", 覆盖率=None, 说明=f"核验异常: {exc}")
            results.append(record)
    return {"manifest": str(manifest_path), "结果": results}


def render_markdown(report: dict, out_path: Path) -> None:
    lines = [
        "# 解析核验报告(自动比对)",
        "",
        f"- manifest: `{report['manifest']}`",
        f"- 文档数: {len(report['结果'])}",
        "",
        "| 判定 | 类别 | 覆盖率 | 口径内 | 未覆盖/需人工 | 逻辑文档 |",
        "|---|---|---|---|---|---|",
    ]
    for r in report["结果"]:
        rate = r.get("覆盖率")
        core = r.get("口径内覆盖率")
        rate_text = "-" if rate is None else f"{rate}%"
        core_text = "-" if core is None else f"{core}%"
        uncovered = r.get("未覆盖", "-")
        manual = r.get("需人工", "-")
        key = r.get("逻辑文档键", "")
        lines.append(
            f"| {r['判定']} | {r['类别']} | {rate_text} | {core_text} | {uncovered}/{manual} | {key} |"
        )
    lines.append("")
    for r in report["结果"]:
        if r["判定"] in {"✅", "✅(带说明)"} and not r.get("未覆盖样例"):
            continue
        lines.append(f"## {r['判定']} {r.get('逻辑文档键','')}")
        lines.append(f"- 源: `{r['源文件']}`")
        if r.get("说明"):
            lines.append(f"- {r['说明']}")
        if r.get("未覆盖样例"):
            lines.append("- 需人工复核未覆盖项:")
            for item in r["未覆盖样例"]:
                text = item.replace("\n", " ⏎ ")[:120]
                lines.append(f"  - {text}")
        if r.get("口径内排除样例"):
            lines.append("- 口径内排除项(样例): " + "; ".join(i.replace(chr(10),' ')[:40] for i in r["口径内排除样例"][:5]))
        lines.append("")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="源文件↔JSON覆盖比对核验器")
    parser.add_argument("manifest", help="batch_convert_all 产出的 manifest.json")
    parser.add_argument("--out", default=None, help="输出 markdown 报告路径")
    parser.add_argument("--min-coverage", type=float, default=0.97)
    parser.add_argument("--max-items", type=int, default=40)
    parser.add_argument("--json-out", default=None, help="同时输出机器可读 JSON")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest).expanduser().resolve()
    report = verify(manifest_path, args.min_coverage, args.max_items)

    out_path = Path(args.out).expanduser() if args.out else manifest_path.parent / "核验报告.md"
    render_markdown(report, out_path)
    if args.json_out:
        Path(args.json_out).expanduser().write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    verdicts = {}
    for r in report["结果"]:
        verdicts[r["判定"]] = verdicts.get(r["判定"], 0) + 1
    print(json.dumps({"报告": str(out_path), "判定分布": verdicts}, ensure_ascii=False))
    bad = sum(v for k, v in verdicts.items() if k in {"⚠️", "❌"})
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
