# 资产清单解析

把 `.xls/.xlsx` 资产清单解析成直接可用的业务 JSON。

每条资产固定保留以下字段，即使源表没有该列或单元格为空，也输出空字符串：

`资产IP、映射IP、资产类型、资产型号、设备厂商、域名、网络位置、定级对象名称、责任人`

源表出现新的列时会自动加入业务对象，任何一列有内容的资产行都不会被丢弃。临时锁文件（文件名以 `.~` 开头）会自动忽略。

单文件：

```bash
python convert.py 输入.xlsx 输出.json
```

批量：

```bash
python batch_convert.py /Users/wenxuanzhang/Desktop/移动/智能体案例
```

核验已生成结果：

```bash
python verify_results.py
```

依赖：`openpyxl`、`xlrd`。不使用 Tika、Java、OCR 或网络模型。
