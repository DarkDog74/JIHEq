# 定级报告嵌套解析器

用于按文档书写顺序解析“智能体案例”目录中的中国移动定级报告模板族。解析器不使用 Tika、Java、OCR 或网络模型：原生 `.docx` 直接读取 OOXML，旧 `.doc` 仅通过 LibreOffice 转换为临时 `.docx`。

## 输出结构

- 第一处一级标题前的内容进入 `前置内容`。
- 一级标题包含二级标题，二级标题包含三级标题。
- 每个章节只有一个有序 `内容` 数组，段落、图片、表格和子章节按原文顺序混合保存。
- 图片提取到与 JSON 同名的 `.assets` 目录，JSON 使用相对地址引用。
- 表格底层采用与备案信息表相同的 OOXML 单元格模型读取，但主 JSON 直接输出业务字段和值，不暴露行号、起始列、跨列数等技术结构。
- 文档信息表输出“文档名称、作者、类别、子类别、文件名称、摘要、修订记录”；等级映射、资产清单和云平台位置均输出字段化记录数组。
- 空字段和空记录仍完整保留；无法归类的普通表格使用 `列1、列2……`，不丢内容也不猜测业务含义。
- 标题识别优先使用 Word 样式，并使用定级报告模板标题别名补全普通正文样式中的三级标题。

## 单份解析

```bash
python convert.py 输入.docx 输出.json
python convert.py 输入.doc 输出.json
```

也可以在 Python 中调用：

```python
from convert import convert

data = convert("输入.docx", "输出.json")
```

## 批量解析

默认扫描 `/Users/wenxuanzhang/Desktop/移动/智能体案例`，输出到本目录下的 `解析结果_嵌套版`：

```bash
python batch_convert.py
```

也可指定目录：

```bash
python batch_convert.py 源目录 输出目录
```

批量结果包含每份报告的嵌套 JSON，以及有图片报告对应的 `.assets` 资源目录。旧的 `解析结果` 目录不会被覆盖。

## 依赖

- Python 3.10+
- `lxml`
- LibreOffice（仅解析 `.doc` 时必须）

## 测试

```bash
python -m unittest discover -s tests -v
```
