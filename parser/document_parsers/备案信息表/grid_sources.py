# -*- coding: utf-8 -*-
"""Tika HTML → 统一网格: 解析 <table>, 按合并单元格去重为每行逻辑格,
并检测首列跨行区块标题(is_section)."""
import json
import re


def _clean(text):
    text = text.replace("\u0007", "").replace("\r", "\n")
    # WPS/Word 勾选框以符号字体渲染, Tika 提取后残留为半角 "(";
    # 仅将 "空白/串首后接中文" 的严格形态还原为 ☑, 避免误伤 (SP12) 等普通括号
    text = re.sub(r"(^|(?<=[\s□]))\((?=[\u4e00-\u9fff])", "☑", text)
    text = re.sub(r"[ \t\u3000]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def grid_from_html(path):
    import lxml.html as LH
    html_text = open(path, "rb").read().decode("utf-8")
    tree = LH.fromstring(html_text)
    table = tree.xpath("//table")[0]
    trs = table.xpath(".//tr")
    grid = {}  # (r,c) -> text; 合并延续位为 None
    occupy = {}
    for r, tr in enumerate(trs):
        c = 0
        for td in tr.xpath("./td|./th"):
            while (r, c) in occupy:
                c += 1
            cs = int(td.get("colspan", 1) or 1)
            rs = int(td.get("rowspan", 1) or 1)
            paras = td.xpath(".//p")
            if paras:
                text = "\n".join(" ".join(p.itertext()) for p in paras)
            else:
                text = " ".join(td.itertext())
            text = _clean(text)
            for dr in range(rs):
                for dc in range(cs):
                    occupy[(r + dr, c + dc)] = True
                    grid[(r + dr, c + dc)] = text if (dr == 0 and dc == 0) else None
            c += cs
    n_rows = len(trs)
    n_cols = max(c for _, c in occupy) + 1
    rows_out = []
    for r in range(n_rows):
        cells, c = [], 0
        while c < n_cols:
            v = grid.get((r, c))
            if v is None:  # 横向/纵向合并的延续格
                c += 1
                continue
            cells.append({"text": v})
            cc = c + 1
            while cc < n_cols and grid.get((r, cc)) is None:
                cc += 1
            c = cc
        rows_out.append(cells)
    return {"source": "tika-html", "n_rows": n_rows, "n_cols": n_cols, "rows": rows_out}


def detect_sections(grid):
    """标记每行第一个逻辑格是否为跨行区块标题:
    a) 同一文本在连续 ≥2 行的首格出现 → 区块标题 (Tika 对纵向合并只在首行输出文本)
    b) 首格为空串且行内还有其他格 → 纵向合并延续
    c) 空首格延续行的上一行即合并起始行, 回溯标记"""
    rows = grid["rows"]
    for r, row in enumerate(rows):
        if row:
            row[0]["is_section"] = False
    for r in range(len(rows)):
        first = rows[r][0] if rows[r] else None
        if not first or len(rows[r]) < 2 or not first["text"]:
            continue
        txt = first["text"]
        n = 1
        while r + n < len(rows):
            nxt = rows[r + n]
            if nxt and nxt[0]["text"] == txt and len(nxt) >= 2:
                n += 1
            else:
                break
        if n >= 2:
            for k in range(r, r + n):
                rows[k][0]["is_section"] = True
    for row in rows:
        if len(row) >= 2 and row[0]["text"] == "":
            row[0]["is_section"] = True
    for r in range(1, len(rows)):
        prev, cur = rows[r - 1], rows[r]
        if (cur and len(cur) >= 2 and cur[0]["text"] == "" and cur[0].get("is_section")
                and prev and len(prev) >= 2 and prev[0]["text"] and not prev[0].get("is_section")):
            prev[0]["is_section"] = True
    return grid


if __name__ == "__main__":
    g = detect_sections(grid_from_html(sys.argv[1]))
    json.dump(g, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
    print(f"{g['n_rows']} rows -> {sys.argv[2]}")
