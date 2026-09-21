from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    ModelResponseFormatError,
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


_PROMPT_TEMPLATE = """你是应急演练图片时间水印核验员。现在只核验演练报告中的一张图片：{label}。

任务：判断这张图片的画面内是否存在清晰可识别的时间水印。

判定口径：
1. 时间水印必须直接呈现在图片画面内，并以水印、拍摄时间叠字或截图时间标记的形式显示日期、时间或日期时间。
2. 文件名、图片名称、EXIF、模型请求文字，以及图片外的报告正文和图注都不算水印。
3. 图片内普通正文、表格、聊天记录或业务界面自身记载的日期，不等于时间水印。
4. 纯地点、经纬度、人员姓名等不含日期或时间的信息，不算时间水印。
5. 看不清、被裁切或无法确认时，必须判定为没有时间水印，不得猜测。
6. 对软件界面截图，若日期时间以独立、高对比的叠字出现在画面角落或边缘，且明显不属于控件、字段、表格、图表坐标轴或日志行，应判定为时间水印；不要仅因底图是软件界面就否定。

只输出一个 JSON 对象，不要输出其他文字：
{{"有时间水印": <true或false>, "水印文字": "<逐字抄录；没有则为空字符串>", "水印位置": "<左上/右下等；没有则为空字符串>", "依据": "<直接视觉证据，60字以内>", "分析说明": "<第一人称概括核验过程，80字以内>"}}"""


def _image_label(item: Mapping[str, Any]) -> str:
    number = item.get("图片序号") or "?"
    marker = str(item.get("图片ID") or "").strip()
    name = str(item.get("图片名称") or "").strip()
    file_name = Path(str(item.get("文件") or "")).name
    details = [value for value in (marker, name, file_name) if value]
    suffix = f"（{' / '.join(dict.fromkeys(details))}）" if details else ""
    return f"第{number}张{suffix}"


def _staged_by_number(fields: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for entry in fields.get("图片文件") or []:
        if not isinstance(entry, Mapping):
            continue
        try:
            number = int(entry.get("图片序号"))
        except (TypeError, ValueError):
            continue
        result[number] = entry
    return result


def _failed_image_result(
    item: Mapping[str, Any], label: str, reason: str
) -> dict[str, Any]:
    return {
        "图片序号": item.get("图片序号"),
        "图片ID": item.get("图片ID"),
        "文件": item.get("文件"),
        "图片名称": item.get("图片名称"),
        "判定": "无法检查",
        "有时间水印": None,
        "水印文字": "",
        "水印位置": "",
        "依据": reason,
        "图片标识": label,
    }


@register_rule("YLBG-05", required_fields=("图片数量", "图片清单"), uses_model=True)
def audit_ylbg_05(fields: Mapping[str, Any]) -> dict[str, Any]:
    images = [
        item for item in (fields.get("图片清单") or []) if isinstance(item, Mapping)
    ]
    if not images:
        return failed_result(
            "YLBG-05",
            ["应急演练报告“演练过程”标题之后至文末未解析出任何真实图片"],
            "演练过程之后没有可检查的图片",
            image_count=0,
            checked_image_count=0,
            watermarked_image_count=0,
            missing_watermark_count=0,
            uncheckable_image_count=0,
            **{"图片判定": []},
        )

    staged = _staged_by_number(fields)
    call_records: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    missing_watermarks: list[str] = []
    uncheckable: list[str] = []

    for item in images:
        label = _image_label(item)
        if item.get("可核验") is not True:
            reason = str(item.get("无法核验原因") or "图片无法读取")
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue

        try:
            number = int(item.get("图片序号"))
        except (TypeError, ValueError):
            reason = "图片序号无效"
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue
        entry = staged.get(number)
        if entry is None:
            reason = "提取阶段未暂存该图片"
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue
        try:
            image_path = resolve_staged_image(entry)
        except StagedImageExpiredError as error:
            reason = str(error)
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue
        if not image_path.is_file():
            reason = "暂存图片文件不存在"
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue

        prompt = _PROMPT_TEMPLATE.format(label=label)
        try:
            reply = _chat_with_images(prompt, [image_path], max_tokens=768)
        except (ModelNotConfiguredError, ModelCallError, OSError) as error:
            reason = f"模型调用失败：{error}"
            call_records.append(call_record(prompt, error=error))
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue

        try:
            payload = extract_json_object(reply.text)
            has_watermark = payload.get("有时间水印")
            if not isinstance(has_watermark, bool):
                raise ModelResponseFormatError(
                    "模型回复缺少布尔字段“有时间水印”"
                )
        except ModelCallError as error:
            reason = f"模型回复无法解析：{error}"
            call_records.append(call_record(prompt, reply=reply, error=error))
            uncheckable.append(f"{label}：{reason}")
            results.append(_failed_image_result(item, label, reason))
            continue

        call_records.append(call_record(prompt, reply=reply))
        basis = str(payload.get("依据") or "").strip()
        result = {
            "图片序号": item.get("图片序号"),
            "图片ID": item.get("图片ID"),
            "文件": item.get("文件"),
            "图片名称": item.get("图片名称"),
            "判定": "有时间水印" if has_watermark else "无时间水印",
            "有时间水印": has_watermark,
            "水印文字": str(payload.get("水印文字") or "").strip(),
            "水印位置": str(payload.get("水印位置") or "").strip(),
            "依据": basis or ("识别到时间水印" if has_watermark else "未识别到时间水印"),
            "图片标识": label,
        }
        results.append(result)
        if not has_watermark:
            missing_watermarks.append(
                f"{label}：{result['依据']}"
            )

    watermarked_count = sum(item.get("有时间水印") is True for item in results)
    missing_count = len(missing_watermarks)
    uncheckable_count = len(uncheckable)
    detail = {
        "image_count": len(images),
        "checked_image_count": len(images) - uncheckable_count,
        "watermarked_image_count": watermarked_count,
        "missing_watermark_count": missing_count,
        "uncheckable_image_count": uncheckable_count,
        "缺少时间水印图片": missing_watermarks,
        "无法检查图片": uncheckable,
        "图片判定": results,
    }
    if missing_watermarks or uncheckable:
        issues = [
            *(f"缺少时间水印：{entry}" for entry in missing_watermarks),
            *(f"无法检查：{entry}" for entry in uncheckable),
        ]
        return failed_result(
            "YLBG-05",
            issues,
            (
                f"共{len(images)}张图片，{missing_count}张缺少时间水印，"
                f"{uncheckable_count}张无法检查"
            ),
            call_records=call_records or None,
            **detail,
        )
    return passed_result(
        "YLBG-05",
        f"应急演练报告“演练过程”之后的全部{len(images)}张图片均有时间水印",
        call_records=call_records,
        **detail,
    )
