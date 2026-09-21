from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    chat as _chat,
    chat_with_images as _chat_with_images,
    extract_json_object,
)

from .model_support import (
    StagedImageExpiredError,
    call_record,
    failed_result,
    passed_result,
    resolve_staged_image,
)
from .registry import register_rule


_PROMPT_TEMPLATE = """下面是某系统本年度的网络安全三同步报告内容（可能为正文 Markdown，也可能为报告图片）。
请先判断报告属于“新改扩”还是“存量”：
1. 报告明确说明本年度或当前系统发生新建、改建、扩建、新增、扩容或升级改造，判为“新改扩”。即使同时描述原有措施，也优先判为“新改扩”。
2. 正确识别否定表述：“未发生新建、改建、扩建”“无新增”之类属于“存量”，不得因为出现“新建/改建/扩建”词语就误判为新改扩。
3. 没有明确发生新改扩的内容，按“存量”处理。存量时还要判断是否有系统/存量情况说明：即提到“原有”“既有”“存量”的设施、系统或网络安全措施仍持续运行、继续有效等。

只依据报告正文/图片内容判断；不要判断文件是否存在，不要直接给出稽核通过或不通过。
只输出下列两种 JSON 结构之一，不要输出其他文字，依据只摘报告原文关键句（50 字以内）：
- 新改扩：{{"场景": "新改扩", "依据": "<报告原文关键句>"}}
- 存量：{{"场景": "存量", "存量说明": "<有|无>", "依据": "<报告原文关键句，没有则填空字符串>"}}

【三同步报告内容】
{body}"""


@register_rule(
    "STB-00",
    required_fields=(
        "三同步正文",
        "图片文件",
        "三同步报告存在",
        "三同步报告清单存在",
    ),
    uses_model=True,
)
def audit_stb_00(fields: Mapping[str, Any]) -> dict[str, Any]:
    body = str(fields.get("三同步正文") or "").strip()
    staged = [item for item in (fields.get("图片文件") or []) if isinstance(item, Mapping)]
    report_exists = bool(fields.get("三同步报告存在", bool(body or staged)))
    report_list_exists = bool(fields.get("三同步报告清单存在", False))
    report_list_files = [
        str(item)
        for item in (fields.get("三同步报告清单文件") or [])
        if str(item).strip()
    ]
    if not report_exists:
        return failed_result(
            "STB-00",
            ["未找到三同步报告"],
            "三同步报告缺失",
        )
    if not body and not staged:
        return failed_result(
            "STB-00",
            ["三同步报告没有可读内容（无正文也无图片）"],
            "三同步报告没有可读内容",
        )
    try:
        image_paths = [resolve_staged_image(entry) for entry in staged]
    except StagedImageExpiredError as error:
        return failed_result("STB-00", [str(error)], "三同步报告图片路径已失效，无法判断")

    if body:
        body_text = body[:20000] + ("…（正文过长已截断）" if len(body) > 20000 else "")
    else:
        body_text = "（报告内容为图片，见随附图片）"
    prompt = _PROMPT_TEMPLATE.format(body=body_text)
    try:
        if image_paths:
            reply = _chat_with_images(prompt, image_paths, max_tokens=1024)
        else:
            reply = _chat(prompt, max_tokens=512)
        payload = extract_json_object(reply.text)
    except (ModelNotConfiguredError, ModelCallError) as error:
        return failed_result(
            "STB-00",
            [f"模型调用失败：{error}"],
            "无法判断三同步报告场景：模型调用失败",
            call_records=[call_record(prompt, error=error)],
        )

    record = [call_record(prompt, reply)]
    detail = {
        "模型依据": payload.get("依据"),
        "图片数量": len(image_paths),
        "三同步报告存在": report_exists,
        "三同步报告清单存在": report_list_exists,
        "三同步报告清单文件": report_list_files,
    }

    scene = str(payload.get("场景") or "").strip()
    if scene not in {"新改扩", "存量"}:
        return failed_result(
            "STB-00",
            [f"模型返回的场景无法识别：{scene or '缺失'}"],
            "无法判断三同步报告场景：模型结论无法识别",
            call_records=record,
            **detail,
        )

    detail["场景"] = scene
    if scene == "新改扩":
        if report_list_exists:
            return passed_result(
                "STB-00",
                "报告内容属于新改扩，三同步报告和报告清单均存在",
                call_records=record,
                **detail,
            )
        return failed_result(
            "STB-00",
            [
                "报告内容属于新改扩，但未找到文件名按顺序包含“三同步…报告…清单”的 Excel 报告清单"
            ],
            "新改扩系统缺少三同步报告清单",
            call_records=record,
            **detail,
        )

    stock_description = str(payload.get("存量说明") or "").strip()
    detail["存量说明"] = stock_description
    if stock_description not in {"有", "无"}:
        return failed_result(
            "STB-00",
            [f"模型返回的存量说明结论无法识别：{stock_description or '缺失'}"],
            "无法判断存量情况说明：模型结论无法识别",
            call_records=record,
            **detail,
        )
    if stock_description == "有":
        return passed_result(
            "STB-00",
            f"三同步报告包含存量情况说明（{payload.get('依据') or '—'}）",
            call_records=record,
            **detail,
        )
    return failed_result(
        "STB-00",
        ["三同步报告未包含存量情况说明（未提及原有设施/系统/安全措施持续运行）"],
        "三同步报告未包含存量情况说明",
        call_records=record,
        **detail,
    )
