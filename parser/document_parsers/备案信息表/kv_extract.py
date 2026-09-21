# -*- coding: utf-8 -*-
"""从统一网格提取 key:value JSON.

表格规律(备案表类文书):
  1) 首格跨行合并  → 区块标题(section), 后续行归入该区块
  2) 行内 [k, v, k, v, ...] 交替 → 直接配对
  3) 区块内首行为全标签行(如 类型*|品牌*|型号*...) → 列头, 其后同列数的行为数据行 → list[obj]
  4) 复选框(□) 值一律视为 value; 全空/纯模板行跳过
"""
import json
import re
import sys

DEBUG = "debug" in sys.argv
MARKS = ("*", "：", ":", "）", ")")
EXCLUDE_KEYS = {"备注"}  # 底部填写说明等非业务字段, 不进结果


def is_label(text):
    if not text:
        return False
    if "□" in text or "☑" in text:
        return False
    if text.rstrip().endswith(MARKS) and len(text) <= 40:
        return True
    return len(text) <= 14 and not re.search(r"\d{2,}", text)


def looks_like_header(texts):
    """表中表表头: ≥2 格, 全部像标签, 且全部以 */：/）等结尾(备案表列头惯例)."""
    if len(texts) < 2 or not all(is_label(t) for t in texts):
        return False
    return all(t.rstrip().endswith(MARKS) for t in texts)


def header_verdict(rows, ri, rest):
    """表头候选行的三级确认(在设置列头前调用):
    header   - 后续有等宽内容行 → 确认为表中表表头
    dangling - 全部强标记(*/：) 但没等到数据行就换了区块/到表尾 → 悬空表头(不产出)
    kv       - 含弱标记格(靠 ）结尾或短文本兜底成标签的)且后续不等宽 →
               本行实为 [标签, 值] 行, 否决回退 k/v 配对
    宽 2 且 [强标记, 无标记] 的形态(如 名称*|…（O域）)自然落入 kv 分支。
    """
    strong = all(t.rstrip().endswith(("*", "：", ":")) for t in rest)
    for rj in range(ri + 1, len(rows)):
        cells_j = [c for c in rows[rj] if c.get("text") is not None]
        texts_j = [c["text"] for c in cells_j]
        if not any(texts_j):
            continue
        first_sec = bool(cells_j[0].get("is_section"))
        if first_sec and texts_j[0]:
            break  # 新区块开始, 此前没有等宽数据行
        rest_j = texts_j[1:] if first_sec else texts_j
        if not rest_j or not any(v for v in rest_j):
            continue
        if len(rest_j) == len(rest):
            return "header"
        if all(is_template(v) for v in rest_j):
            continue  # 宽度不一的模板行, 继续找真数据行
        break
    return "dangling" if strong else "kv"


def is_template(v):
    """空串或纯复选框模板值(去掉□/括号/空白后几乎无内容)."""
    v = v.strip()
    if v == "":
        return True
    if "☑" in v:  # 已有选中项, 不是模板
        return False
    if "□" in v:
        core = re.sub(r"[\s□（）()：:、,，]", "", v)
        return len(core) <= 4
    return False


BOX = re.compile(r"([□☑])([^□☑]*)", re.S)


def checked_only(v):
    """□/☑ 选项值 → 仅保留 ☑ 选中的项; 无选中项返回空串; 非选项值原样返回."""
    if "□" not in v and "☑" not in v:
        return v
    segs = []
    for m in BOX.finditer(v):
        if m.group(1) == "☑":
            t = m.group(2).strip()
            t = re.sub(r"[：:]\s*$", "", t).strip()
            if t:
                segs.append(t)
    return "；".join(segs)


def collapse_result(result):
    """对整棵结果树应用 checked_only."""
    out = {}
    for k, v in result.items():
        if isinstance(v, str):
            out[k] = checked_only(v)
        elif isinstance(v, list):
            out[k] = [
                {ik: checked_only(iv) if isinstance(iv, str) else iv
                 for ik, iv in item.items()} if isinstance(item, dict)
                else (checked_only(item) if isinstance(item, str) else item)
                for item in v
            ]
        else:
            out[k] = v
    return out


def norm_key(text):
    """键名规范化: 竖排换行合并、空白压缩."""
    if len(text) <= 30:
        text = re.sub(r"\s*\n\s*", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def extract_kv(grid):
    rows = grid["rows"]
    result = {}
    section = None       # 当前区块标题
    col_headers = None   # 当前区块的列头(表中表)

    def put(key, value):
        key = norm_key(key)
        if not key or key in EXCLUDE_KEYS:
            return
        if key in result and result[key] != value:
            old = result[key]
            result[key] = old if isinstance(old, list) else [old]
            result[key].append(value)
        else:
            result[key] = value

    def add_record(headers, values):
        key = norm_key(section or headers[0]).rstrip("*").strip()
        obj = {norm_key(h): v for h, v in zip(headers, values)}
        result.setdefault(key, [])
        if isinstance(result[key], list):
            result[key].append(obj)

    for ri, row in enumerate(rows):
        cells = [c for c in row if c.get("text") is not None]
        texts = [c["text"] for c in cells]
        if not any(texts):
            continue
        first_is_section = bool(cells[0].get("is_section"))
        if DEBUG:
            print(f"[{ri:2}] sec={section!r} hdr={None if col_headers is None else len(col_headers)} "
                  f"secMark={first_is_section} cells={[t[:8] for t in texts]}")

        if first_is_section:
            new_sec = norm_key(texts[0])
            if new_sec and new_sec != section:
                section, col_headers = new_sec, None
            # 空首格 = 纵向合并延续, 沿用当前 section 与列头
            rest = texts[1:]
        else:
            if texts[0] != (section or texts[0]):
                # 首格既不是区块延续也不是当前区块 → 独立行, 退出区块上下文
                section, col_headers = None, None
            rest = texts

        if not rest:  # 整行只有区块标题
            continue
        if not any(v for v in rest):  # 全空行
            continue

        # 表中表表头(三级确认: 等宽确认 → 设列头; 否决 → 落到下方 k/v 配对)
        if col_headers is None and looks_like_header(rest):
            if header_verdict(rows, ri, rest) != "kv":
                col_headers = [norm_key(t) for t in rest]
                continue
        # 表中表数据行
        if col_headers is not None and len(rest) == len(col_headers):
            if all(is_template(v) for v in rest):
                continue  # 纯模板/空行
            add_record(col_headers, rest)
            continue

        # 普通 [k,v,(k,v)...] 配对
        if len(rest) >= 2:
            i = 0
            row_prefix = None  # 行级标签(如 .cn域名相关记录|NS记录| |A记录| )
            while i < len(rest):
                k, v = rest[i], (rest[i + 1] if i + 1 < len(rest) else "")
                # 空占位/「无」(部分表以"无"填空白格)后接标签 → 对齐错位, 后移一格
                if (is_template(k) or k == "无") and i + 1 < len(rest) \
                        and is_label(rest[i + 1]) and not is_template(rest[i + 1]):
                    i += 1
                    continue
                # 标签+标签+空白占位签名 → k 是行级标签, 作前缀后重新配对
                if (row_prefix is None and is_label(k) and k and is_label(v) and v
                        and i + 2 < len(rest) and rest[i + 2] in ("", "无")):
                    row_prefix = k
                    i += 1
                    continue
                i += 2
                if is_template(k) or k == "无":
                    continue
                full_k = f"{row_prefix or section}·{k}" if (row_prefix or section) else k
                put(full_k, v.strip())
        else:
            if not is_template(rest[0]):
                put(rest[0], "")
    return collapse_result(result)


if __name__ == "__main__":
    grid = json.load(open(sys.argv[1]))
    kv = extract_kv(grid)
    json.dump(kv, open(sys.argv[2], "w"), ensure_ascii=False, indent=1)
    print(f"{len(kv)} top-level keys -> {sys.argv[2]}")
