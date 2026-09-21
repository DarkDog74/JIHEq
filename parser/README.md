# anydoc 文件夹解析工具

这个工具的核心是一个 Python 脚本：`parse_folder.py`。每次接收一个业务系统文件夹，先分析所有文件。十类材料都会进入实际处理，结果直接放在该业务系统的输出目录下。

## 支持的 10 类文件

| 规范名称 | 常见识别文字 |
|---|---|
| 2.1 定级报告 | 定级报告 |
| 2.2 备案信息表 | 备案信息表、备案表 |
| 2.3 资产清单 | Excel 文件名含资产清单或资产表；即使同时含“定级报告”也优先按资产处理 |
| 2.4 三同步报告 | 三同步 |
| 2.5 符合性评测表 | 符合性评测、符合性测评、符合性检查 |
| 2.6 符合性评测表证明材料 | 文件名同时存在“符合性”和“证明”（两个词不要求连续），或者文件名包含“证明材料” |
| 2.7 风险评估报告 | 风险评估 |
| 2.8 扫描报告 | 包含HTML的ZIP，或路径/文件名含扫描、漏扫、漏洞、弱口令、渗透、基线 |
| 2.9 符合性评测报告 | 文件名包含“符合性评测/测评/检查”和“报告”，排除评测表与证明材料 |
| 2.10 演练计划 | Excel 文件名包含“演练计划” |

当前定级报告严格只支持Word的`.doc`和`.docx`；备案信息表支持`.doc`、`.docx`和`.wps`；资产清单、符合性评测表和演练计划支持Excel的`.xls`和`.xlsx`；三同步报告支持`.doc`、`.docx`、`.pdf`、`.png`、`.jpg`和`.jpeg`；符合性评测表证明材料与风险评估报告支持`.doc`、`.docx`和`.pdf`；符合性评测报告支持`.doc`和`.docx`；扫描报告支持`.zip`，只读入口HTML并生成汇总JSON，不全量解压。不支持的格式只会在分析清单中标记为“格式不支持”。

## 批量处理多个业务系统文件夹

批量入口`batch_parse_folders.py`严格把“部门目录下的直接子文件夹”作为一个业务系统单位，不会把扫描报告等更深层目录拆开投入：

```bash
.venv/bin/python batch_parse_folders.py "/Users/wenxuanzhang/Desktop/网管中心"
```

默认集中输出为：

```text
网管中心/批量解析结果/
├── 批量汇总.json
├── 批量汇总.csv
├── 业务平台部/
│   └── 原业务系统文件夹名/
│       ├── 定级报告.md
│       ├── 备案信息表.md
│       ├── 备案信息表.json
│       ├── 资产清单.md
│       ├── 符合性评测表.md
│       ├── 符合性评测报告.md
│       ├── 演练计划.md
│       ├── 符合性评测表证明材料.md
│       ├── 风险评估报告.md
│       ├── 应急预案.md
│       ├── 演练方案.md
│       ├── 应急演练报告.md
│       ├── 应急材料分析.json
│       ├── 文件分析.json
│       ├── 文件分析.md
│       └── 附件/
├── 网管支撑部/
│   └── 原业务系统文件夹名/
└── ...
```

已有测试集、`*_anydoc解析结果`和旧结果备份会自动排除。重新运行时使用`--overwrite`，旧批量结果会先被整体备份。

## 第一次安装

在本文件夹打开终端，执行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

## 使用方法

```bash
.venv/bin/python parse_folder.py "/待处理的业务系统文件夹"
```

也可以指定输出位置：

```bash
.venv/bin/python parse_folder.py "/待处理文件夹" -o "/输出文件夹"
```

只进行文件分析，不解析正文：

```bash
.venv/bin/python parse_folder.py "/待处理文件夹" --analysis-only
```

如果输出目录已经存在，并且确定要重新生成：

```bash
.venv/bin/python parse_folder.py "/待处理文件夹" --overwrite
```

`--overwrite` 不会直接删除旧结果，而是先将它重命名为带时间的“旧结果备份”文件夹。

## 应急预案与应急演练 Word 解析

`emergency_document_adapter.py` 是一个按单份源文件调用的公共适配器，不将应急材料注册成新的 2.x 业务类别。它支持 `.doc/.docx`，保留正文、表格、图片原位标记和附件；不处理 WPS 和 PDF。

Python 调用：

```python
from parser.emergency_document_adapter import (
    parse_emergency_drill,
    parse_emergency_plan,
    write_emergency_markdown,
)

result = parse_emergency_plan(source_path, output_dir, stem="应急预案")
write_emergency_markdown(result, output_dir / "应急预案.md")

result = parse_emergency_drill(source_path, output_dir, stem="应急演练")
write_emergency_markdown(result, output_dir / "应急演练.md")
```

命令行调用：

```bash
.venv/bin/python parser/emergency_document_adapter.py "/原文件/应急预案.docx" "/输出/应急预案.md" --kind plan
.venv/bin/python parser/emergency_document_adapter.py "/原文件/应急演练方案.doc" "/输出/演练方案.md" --kind drill
```

`.doc` 会继续使用 LibreOffice 转换为临时 DOCX，不会修改原文件。

`emergency_material_standardizer.py` 会先解析命中的全部应急预案、严格命名的演练方案和应急演练报告。演练方案要求文件名同时包含“演练”和“方案”；只有“应急方案”的设备运行维护手册不计入。预案和报告分别按“修订记录中的最新日期”和“演练时间”降序排列：

- 最新一份：`应急预案.md` / `应急演练报告.md`
- 第二新：`上一份应急预案.md` / `上一份应急演练报告.md`
- 更早材料：保留为带日期的历史 Markdown
- 演练方案：当前标准文件为 `演练方案.md`，如有多份则保留上一份和带日期的历史 Markdown

每个系统都会生成 `应急材料分析.json`，记录原文件、排序日期、序号、标准角色和标准 Markdown。可在已有解析结果上增量执行：

```bash
.venv/bin/python parser/parse_folder.py "/某系统原件目录" \
  -o "/该系统解析结果目录" --emergency-only --overwrite

.venv/bin/python parser/batch_parse_folders.py "/网管中心" \
  -o "/网管中心/批量解析结果" --emergency-only --overwrite
```

## 输出结果

输出文件夹类似：

```text
业务系统文件夹_anydoc解析结果/
├── 文件分析.json
├── 文件分析.md
├── 定级报告.md
├── 备案信息表.md
├── 备案信息表.json
├── 资产清单.md
├── 三同步.md / 三同步.png
├── 三同步.json
├── 符合性评测表.md
├── 符合性评测报告.md
├── 演练计划.md
├── 符合性评测表证明材料.md
├── 风险评估报告.md
├── 应急预案.md
├── 演练方案.md
├── 应急演练报告.md
├── 应急材料分析.json
└── 附件/
    └── 定级报告/
```

备案信息表会同时生成anydoc原生Markdown和完整中文KV JSON。JSON直接读取Word合并单元格，除“备注”外保留所有字段、空值和必填标记。符合性评测表直接使用anydoc生成原生Markdown，不另外生成JSON。三同步按“PNG/JPG原图 > PDF > Word”选择输入：已有原图时不再处理PDF和Word，没有原图时将PDF逐页渲染为PNG，只有Word时才用anydoc生成`三同步.md`并导出内嵌图片。单张图片命名为`三同步.png/jpg`，多张从`三同步-002`开始编号；`三同步.json`列出后续模型应读取的全部相对路径。风险评估报告保留anydoc原生正文，并按下文“图片标识”规则补充图片证据。符合性评测表证明材料使用专用解析器按稽核编号分节，保留段落、表格和截图。其他类别的同名Word/PDF仍优先`.docx` > `.doc` > `.pdf`。

批量处理时会额外生成`批量失败明细.md`。如果解析失败，该文件会列出业务系统、失败原件、明确原因、处理建议和底层技术错误；例如区分DOCX损坏、Word内部图片引用失效和一般解析异常。单项重跑时，终端统计只计算本次选择的类别，不再混入以前已生成的其他类别结果。

## Word 页眉

定级报告 Markdown 的第一行固定为：

```markdown
<!-- 页眉：默认页眉=示例系统 定级报告 -->
```

如果 Word 没有页眉，或输入不是 Word，则第一行为：

```markdown
<!-- 页眉：空 -->
```

页眉逻辑位于独立模块 `word_header.py`，可供后续其他文档解析器直接复用：

```python
from word_header import extract_word_header, prepend_header_comment

header = extract_word_header(word_path)
markdown = prepend_header_comment(markdown, header)
```

- `.docx`：直接读取OOXML中各节的默认、首页、偶数页页眉及字段显示值。
- `.doc`：用 LibreOffice 转换成临时 DOCX 后读取；不会修改源文件。
- 页眉的字段代码、来源部件和分节信息同时写入 `文件分析.json`。

## 图片标识

对于Word定级报告和Word风险评估报告，脚本使用`to_document()`读取图片节点，并通过临时alt令牌继续调用anydoc原生Markdown渲染器。在图片原来的逻辑位置写入可见标识和保存地址，其他标题、表格和列表仍由anydoc原生渲染：

```markdown
[[IMAGE id=IMG-001 file="附件/定级报告/IMG-001.png" alt="拓扑图" type="image/png"]] ![拓扑图](<附件/定级报告/IMG-001.png>)
```

图片没有alt文字时仍然保留：

```markdown
[[IMAGE id=IMG-001 file="附件/定级报告/IMG-001.png" alt="" type="image/png"]] ![IMG-001](<附件/定级报告/IMG-001.png>)
```

OLE/Visio等Word内嵌对象也按图片口径使用`[[IMAGE ...]]`和`IMG-xxx`编号，附件仍保留原始`.bin`内容和`application/vnd.ms-ole-object`类型。因此后续按`[[IMAGE]]`统计时，拓扑章节中的OLE对象不会再被误判为0张图。每个图片/对象的`marker_id`、文件、类型、alt和`blocks[...]`逻辑位置也会写入`文件分析.json`的`images`字段。

PDF风险评估报告先保留anydoc原生文字和表格；如果页面文字含“拓扑图”，再将该页及下一页渲染为PNG附件，并在对应拓扑文字后写入`[[IMAGE ...]]`。这样既覆盖标题与图片同页的报告，也覆盖标题位于前一页、图片位于下一页的常见模板。

风险评估报告解析失败时，输出会明确写入`PARSE_ERROR`标识。稽核层会将其作为解析异常处理，不会把残缺存根当成正常报告。

定级报告目前不处理PDF。

`文件分析.md` 用于直接查看分类数量和处理情况。`文件分析.json` 会记录原文件名、分类、文件大小、哈希、解析状态、规范输出名、导出的附件数量和错误原因，可直接供后续 Python 程序读取。
