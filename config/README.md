# 路径配置

编辑 `paths.json` 可集中修改项目运行所需的路径和外部服务地址。

- `evaluation_root`：评估集默认根目录（`run_评估集20.sh` 使用）。
- `default_output_root`：默认结果根目录。
- `sample_source_root`：各独立解析脚本未传入源目录时使用的目录。
- `soffice_candidates`：LibreOffice 可执行文件候选路径。
- `mineru_url`：MinerU 服务接口地址。

- 相对路径以项目根目录为基准；绝对路径可直接填写。
- 对应环境变量（如 `AUDIT_EVALUATION_ROOT`）优先于此文件，适合临时覆盖。
- `soffice_candidates` 可填写 LibreOffice 可执行文件路径列表。
- 不要把 API Key 等密钥写入此文件；密钥仍由平台配置或 `secrets.env` 管理。
