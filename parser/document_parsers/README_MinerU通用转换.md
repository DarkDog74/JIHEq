# MinerU 通用内容转换器

入口：`convert_mineru_results.py`

如果输入是一个包含七类材料的最低级业务文件夹，使用 `run_mineru_folder_workflow.py`；它会先完成分类和格式检查，再调用 MinerU 和本转换器。详见 `README_MinerU文件夹工作流.md`。

它读取 MinerU 返回的 ZIP 或解压目录，优先处理 `*_content_list_v2.json`，生成按原文顺序嵌套的标题、段落、文本、表格、图片和公式 JSON。

```bash
python convert_mineru_results.py \
  "/path/to/mineru_result.zip" \
  "/path/to/output"
```

也可以直接读取解压目录：

```bash
python convert_mineru_results.py \
  "/path/to/mineru_extracted" \
  "/path/to/output"
```

输出包括：

- 每份文档的 `.json`；
- 每份文档的 `.audit.json`；
- 每份文档的 `.assets` 图片目录；
- 总体 `转换汇总.json`。

表格会自动识别为：

- `键值表`：输出一个字段对象；
- `记录表`：输出对象数组；
- `混合表`：分别保存表内的基本信息和子表；
- `无表头记录`：保留列数据和原始矩阵，不猜测字段名。

`rowspan`、`colspan` 会被恢复为合并单元格信息；已知表头下的空值会保留为 `""`，`/`、`无`、`0`、`未发现问题`不会被过滤。

跨页长表会按以下条件合并：连续页面、列数一致、处于同一内容位置，且后续块没有独立表头或使用文档页眉作为表名。后续块继承第一块字段名；如果页首行明显比正常数据行稀疏，则把它作为上一页末行的续写逐列拼接。合并页码、继承字段和行拼接情况会记录在 `.audit.json` 的 `continuation_merges` 中。
