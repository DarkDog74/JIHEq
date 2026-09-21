# 七类材料统一解析

入口：

```text
python batch_convert_all.py 智能体案例 统一解析结果
```

选源规则：Word 类 `.docx > .doc > .pdf`，Excel 类 `.xlsx > .xls > .pdf`。同一逻辑文档的 PDF 按 SHA256 去重。有原件时 PDF 仅记录在 `manifest.json` 和对应 `.audit.json` 的“未采用候选”，不重复生成业务 JSON。

PDF 后端全程本地运行，轻量依赖见 `requirements-pdf.txt`。纯扫描页才调用 RapidOCR；主 JSON 继续使用各类别原有 Schema，来源、引擎、置信度、异常与跳过候选只写审计文件。

25 份原件/PDF配对验收结果写在统一结果目录的 `pdf_pair_validation.json`。后端路由由 `pdf_backend_policy.json` 控制：未达到类别级阈值的类别强制使用本地 MinerU pipeline，依赖见 `requirements-mineru.txt`。如果 MinerU 或本地模型不存在，解析器只生成 `error` 审计，不生成不完整主 JSON。三同步纯扫描 PDF 继续使用已人工核验通过的轻量 OCR 后端。

可先检查选源，不执行转换：

```text
python batch_convert_all.py 智能体案例 统一解析结果 --dry-run
```

配对验收：

```text
python validate_pdf_pairs.py 统一解析结果/manifest.json 统一解析结果/pdf_pair_validation.json
```
