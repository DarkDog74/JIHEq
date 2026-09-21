# Word 三同步报告解析器

本目录独立解析 `.doc/.docx` 格式的三同步报告，不处理 JPG、PDF、XLSX 等其他格式。

输出采用固定业务字段，同时用 `内容` 数组按原文顺序保留全部非空段落、图片和表格：

- 固定字段即使未识别到内容也保留为空字符串；
- 图片提取到与 JSON 同名的 `.assets` 文件夹，JSON 中保存相对地址；
- 表格同时保留字段、逐行记录、原始二维数据和合并关系；
- 每份主 JSON 同时生成一个 `.audit.json`，记录哈希、数量和需要复核的问题；
- 旧 `.doc` 仅通过 LibreOffice 转换为临时 `.docx`，不使用 Tika、Java、OCR 或网络模型。

## 单份解析

```text
python convert.py 输入.docx 输出.json
```

## 批量解析

```text
python batch_convert.py
```

默认扫描 `/Users/wenxuanzhang/Desktop/移动/智能体案例`，仅选择文件名中同时包含“三同步”和“报告”的 Word 文件，结果写入本目录下的 `解析结果`。
