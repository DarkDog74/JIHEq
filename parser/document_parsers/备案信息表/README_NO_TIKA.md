# 备案信息表解析器（无 Tika/Java）

本解析器面向现有备案信息表模板族，不依赖 Apache Tika、Java 或任何网络视觉接口。

## 处理链路

1. LibreOffice 从原始 `.doc/.docx` 直接生成 PDF；
2. `.doc` 另外转为临时 DOCX，`.docx` 直接读取原 OOXML；
3. 直接解析 `document.xml` 中的表格网格、合并格、文本和符号；
4. 用 PDF 字符坐标/本地图像核验复选框，无坐标时可选用 RapidOCR；
5. 按备案表 Schema 生成业务 JSON，同时生成独立审计 JSON。

PDF 有明确高置信状态时作为最终证据。如 LibreOffice 在 PDF 中自身漏渲染符号，解析器会保留已识别的高置信 OOXML 证据并写入审计，不会将真实已选项错误清空。

## 环境

- 系统需安装 LibreOffice，或通过 `LIBREOFFICE_PATH` 指定 `soffice`；
- Python 依赖见 `requirements.txt`；
- RapidOCR 仅在 PDF 没有可用字符坐标时启用。

## 调用

```bash
python convert.py 输入.doc 输出.json
```

产生：

- `输出.json`：业务数据，不含解析元数据；
- `输出.audit.json`：文档哈希、转换、表格结构、复选框证据、过滤记录和源文档异常。

业务 JSON 按固定 Schema 输出所有顶层和子字段：空标量为空字符串 `""`；没有有效记录的表格区块保留一个全字段空对象，并在审计文件的 `schema_placeholders` 中明确标记，避免被误认为源文档中的真实记录。

记录过滤遵循“源文保真”：除序号外，只要任一映射字段含有原文内容，包括 `/`、`XX/XXX` 占位文字或默认“未发现问题”，整条记录都会保留。只有所有业务字段均为空的行才会丢弃。

Python 接口保持不变：

```python
from convert import convert

data = convert("input.doc", "output.json")
```

致命转换或结构错误只写 `audit.json`，不写不完整主 JSON。

## 测试

快速单元测试：

```bash
python -m unittest discover -s ../tests -p 'test_no_tika_parser.py' -v
```

10 份原文档端到端验收：

```bash
RUN_FILING_E2E=1 python -m unittest discover -s ../tests -p 'test_no_tika_parser.py' -v
```

验收真值在 `../tests/fixtures/no_tika_manual_acceptance.json`，根据逐页人工核验报告录入，测试中明确拒绝启动 Tika/Java。
