# 符合性评测表解析（含图片）

这是独立于“符合性评测表”纯字段版的新解析器。它保留相同的业务字段结构，并在每条稽核项中增加固定的 `图片` 数组。

```json
{
  "答案说明": "有图",
  "图片": [
    {
      "图片编号": "IMG001",
      "地址": "报告名.assets/IMG001.png",
      "来源": "单元格图片",
      "所在字段": "答案说明"
    }
  ]
}
```

## 图片处理规则

- WPS `DISPIMG` 单元格图片：使用图片 ID 精确匹配 WPS 图片登记表和媒体文件。
- 普通浮动图片：使用 OOXML 工作表锚点定位到稽核项和字段。
- OLE 对象预览图：解析 VML 锚点并保留原始 PNG/EMF 文件。
- 同一媒体被多行重复使用时只保存一份，所有出现位置均保留引用。
- 图片单元格没有普通文字时，对应业务字段标记为 `有图`。
- 无法挂到稽核项的图片进入顶层 `其他图片`，不会丢弃。
- 每份 JSON 对应一个同名 `.assets` 文件夹。

旧版 `.xls` 的 WPS 图片流由代码直接解析，不依赖 `olefile`；LibreOffice 仅用于读取旧版表格中的普通浮动图片。

## 使用方式

单文件：

```bash
python convert.py 输入.xls 输出.json
```

批量：

```bash
python batch_convert.py /Users/wenxuanzhang/Desktop/移动/智能体案例
```

核验结果和图片地址：

```bash
python verify_results.py
```

依赖：`openpyxl`、`xlrd`、LibreOffice。未使用 Tika、Java、OCR 或网络视觉模型。
