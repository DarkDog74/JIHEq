# 七类材料检查、备案 Schema 与 MinerU 自动解析

入口：`run_mineru_folder_workflow.py`

将最低级业务文件夹作为唯一输入：

```bash
python run_mineru_folder_workflow.py "/path/to/业务文件夹"
```

默认调用：

```text
http://127.0.0.1:18001/file_parse
backend=hybrid-engine
effort=medium
parse_method=auto
```

可以先只检查文件，不调用 MinerU：

```bash
python run_mineru_folder_workflow.py "/path/to/业务文件夹" --dry-run
```

## 分类和处理

| 类别 | 合格格式 | 动作 |
|---|---|---|
| 定级报告 | PDF | MinerU |
| 备案信息表 | DOC/DOCX | 备案固定 Schema 本地解析，不进 MinerU |
| 资产清单 | XLS/XLSX | 只检查 |
| 三同步报告 | PDF/JPG/JPEG/PNG | MinerU |
| 符合性评测表 | XLS/XLSX | 只检查 |
| 符合性评测证明文件 | DOC/DOCX | 编号章节、内容和图片本地解析，不进 MinerU |
| 风险评估报告 | PDF | MinerU |

分类只使用文件名关键词，递归检查子目录。同一类别出现多个合格文件时，按完整路径排序采用第一个，其余文件不处理并记录在 `manifest.json` 的“未采用合格候选”中。

- 备案信息表只采用 `.doc/.docx`：直接调用 `备案信息表/convert.py`，输出固定业务字段、空字段和完整子表 Schema，不保留 MinerU 通用结构中的多余字段。
- 符合性评测证明文件只采用 `.doc/.docx`：每个 `XX-XX-XX-XX-XX` 编号或编号组合建立一个章节，章节标题只保留编号，说明文字和图片按原文顺序放入该章节的 `内容`。
- 备案和证明材料的 PDF 都不作为主 JSON 输入。单独的“符合性评测报告”也不会被当成“符合性评测证明文件”。

需要正式处理的仍是5类：备案信息表和符合性评测证明文件本地解析；定级报告、三同步报告和风险评估报告共3类进入 MinerU。如果这5类不足5份，程序会先列出已合格类别和未进入原因，然后等待终端确认：

```text
是否继续处理已合格的文件？输入 y 继续，其他输入取消：
```

只有输入 `y`或`Y`才会继续调用 MinerU；其他输入、直接回车或中断都会取消。5类全部合格时不询问，直接处理。

## 输出

每次运行都在输入文件夹内创建独立目录：

```text
解析结果/
└── run_YYYYMMDD_HHMMSS/
    ├── manifest.json
    ├── mineru_result.zip       # 存在MinerU输入时生成
    └── 通用JSON/
        ├── 各类材料.json
        ├── 各类材料.audit.json
        ├── 各类材料.assets/
        └── 转换汇总.json
```

`备案信息表.json` 与既有备案解析器的字段完全一致；`符合性评测证明文件.json` 是编号章节、内容和图片结构；定级、三同步和风险报告继续使用 MinerU 通用结构。

`manifest.json` 是整次运行的总清单，包含七类检查结果、采用文件、错误格式、冲突、SHA256、MinerU 状态和最终 JSON 路径。

## 返回状态

- `0`：七类检查及全部解析通过。
- `2`：部分缺失、格式不合格、冲突或部分解析失败，但已有可用 JSON。
- `1`：输入无效、没有可处理文件或 MinerU 整体失败。

可选参数：

```bash
python run_mineru_folder_workflow.py INPUT \
  --mineru-url "http://127.0.0.1:18001/file_parse" \
  --effort medium
```

无人值守批处理可以使用 `--yes`，不足5类时自动确认继续：

```bash
python run_mineru_folder_workflow.py INPUT --yes
```
