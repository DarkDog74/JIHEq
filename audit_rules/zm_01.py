from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    chat_with_images as _chat_with_images,
    extract_json_object,
)

from .model_support import (
    call_record,
    confidence_of,
    CONFIDENCE_THRESHOLD,
    failed_result,
    passed_result,
    resolve_staged_image,
    review_result,
    StagedImageExpiredError,
)
from .registry import register_rule

_PROMPT_TEMPLATE = """这是通信网络安全防护符合性评测中检查项 {code} 的证明材料截图（共 {count} 张）。
该检查项的说明文字如下：
【说明】{description}

请判断：截图内容是否是上述说明所述措施/配置/界面的有效证据。
只输出一个 JSON 对象，不要输出其他文字，依据只写截图关键内容（40 字以内）：
{{"对应": <true或false>, "无法判断": <true或false>, "依据": "<截图关键内容，40字以内>", "置信度": <0-1之间的数值>}}"""

_CODE_IN_NOTE_RE = re.compile(r"检查项\s+(\d{2}-\d{2}-\d{2}-\d{2}-\d{2})")


def _screenshot_paths_by_code(fields: Mapping[str, Any]) -> dict[str, list[Path]]:
    paths: dict[str, list[Path]] = {}
    for entry in fields.get("图片文件") or []:
        if not isinstance(entry, Mapping):
            continue
        match = _CODE_IN_NOTE_RE.search(str(entry.get("说明") or ""))
        if match is None:
            continue
        paths.setdefault(match.group(1), []).append(resolve_staged_image(entry))
    return paths


def _judge_item(code: str, description: str, image_paths: list[Path]) -> tuple[str, str, list[dict[str, Any]]]:
    """返回 (判定: 对应|不对应|待人工, 依据, 调用留档)；单次失败重试一次。"""
    prompt = _PROMPT_TEMPLATE.format(
        code=code, count=len(image_paths), description=description[:500]
    )
    records: list[dict[str, Any]] = []
    last_error: Exception | None = None
    for _attempt in range(2):
        try:
            reply = _chat_with_images(prompt, image_paths, max_tokens=1024)
        except (ModelNotConfiguredError, ModelCallError) as error:
            last_error = error
            records.append(call_record(prompt, error=error))
            continue
        record = call_record(prompt, reply)
        records.append(record)
        try:
            payload = extract_json_object(reply.text)
        except ModelCallError as error:
            last_error = error
            continue
        confidence = confidence_of(payload)
        if payload.get("无法判断") is True or confidence is None or confidence < CONFIDENCE_THRESHOLD:
            return "待人工", str(payload.get("依据") or "模型无法判断或置信度不足"), records
        if payload.get("对应") is True:
            return "对应", str(payload.get("依据") or "—"), records
        return "不对应", str(payload.get("依据") or "—"), records
    return "待人工", f"模型调用失败：{last_error}", records


@register_rule("ZM-01", required_fields=("检查项",), uses_model=True)
def audit_zm_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    items = [item for item in (fields.get("检查项") or []) if isinstance(item, Mapping)]
    if not items:
        return failed_result("ZM-01", ["证明材料中未提取到检查项"], "未提取到检查项")

    try:
        paths_by_code = _screenshot_paths_by_code(fields)
    except StagedImageExpiredError as error:
        # 异常消息本身已带“staged 图片路径已失效”前缀，直接复用避免双重前缀
        return review_result("ZM-01", str(error), **{"检查项判定": []})
    records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    pending: list[str] = []

    for item in items:
        code = str(item.get("检查项编号") or "")
        description = str(item.get("说明文字") or "")
        screenshots = [shot for shot in (item.get("截图") or []) if isinstance(shot, Mapping)]
        try:
            image_paths = paths_by_code.get(code) or [
                resolve_staged_image(entry)
                for entry in (fields.get("图片文件") or [])
                if isinstance(entry, Mapping)
                and str(entry.get("id") or "") in {str(shot.get("id") or "") for shot in screenshots}
            ]
        except StagedImageExpiredError as error:
            pending.append(f"{code}：图片路径已失效，待人工复核")
            results.append({"检查项编号": code, "判定": "待人工", "依据": str(error)})
            continue
        if not screenshots or not image_paths:
            failures.append(f"{code}：未提供截图")
            results.append({"检查项编号": code, "判定": "不对应", "依据": "未提供截图"})
            continue
        verdict, basis, item_records = _judge_item(code, description, image_paths)
        records.extend(item_records)
        results.append({"检查项编号": code, "判定": verdict, "依据": basis})
        if verdict == "不对应":
            failures.append(f"{code}：{basis}")
        elif verdict == "待人工":
            pending.append(f"{code}：{basis}")

    detail = {"检查项判定": results}
    if pending:
        issues = [f"待人工复核 {entry}" for entry in pending]
        if failures:
            issues.extend(failures)
        return review_result(
            "ZM-01",
            f"{len(pending)} 个检查项需人工判断，" + "；".join(pending[:5]),
            call_records=records or None,
            **detail,
        )
    if failures:
        return failed_result(
            "ZM-01",
            failures,
            f"{len(failures)} 个检查项截图与说明不对应或缺截图",
            call_records=records or None,
            **detail,
        )
    return passed_result(
        "ZM-01",
        f"{len(items)} 个检查项截图与说明均对应",
        call_records=records or None,
        **detail,
    )
