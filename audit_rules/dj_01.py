from __future__ import annotations

from typing import Any, Mapping

from core.model_client import (
    ModelCallError,
    ModelNotConfiguredError,
    chat as _chat,
    extract_json_object,
)

from .model_support import (
    ReferenceDocumentMissingError,
    call_record,
    failed_result,
    load_reference,
    passed_result,
)
from .registry import register_rule


_GENERIC_SECTION_LABEL = "其他业务系统"


_PROMPT_TEMPLATE = """你是通信网络安全定级稽核专家。某系统在定级报告中给出了三要素赋值理由，请依据
《网络和系统定级要素赋值细化指标》判断每项赋值是否符合标准。

【符合性评测表标题的网络类型】{unit_type}
【标准材料】
{standard}

【社会影响力——报告赋值 {I}，理由原文】{reason_I}
【规模和服务范围——报告赋值 {R}，理由原文】{reason_R}
【提供服务的重要性——报告赋值 {V}，理由原文】{reason_V}

要求：
1. 先在标准中定位该单元类型对应的赋值表（章节/表号写进依据）；
2. 再根据每段理由描述的实际情况，按标准判定应取赋值；标准为同一要素定义多个可选指标（如R1、R2）时，只按理由中实际提供的指标定档，理由未提及的指标不参与、也不作为判不了的原因；仅当理由已提供的指标按标准仍无法定档时，才在“标准赋值”填 null 并在依据里写明原因；
3. 每条依据只写“标准表号+关键事实”，控制在 40 字以内，不要展开论述；
4. 只输出一个 JSON 对象，不要输出其他文字：
{{"要素结论": [
  {{"要素": "社会影响力", "标准赋值": <1-5的整数或null>, "依据": "<标准表号+关键事实，40字以内>"}},
  {{"要素": "规模和服务范围", "标准赋值": <1-5的整数或null>, "依据": "..."}},
  {{"要素": "提供服务的重要性", "标准赋值": <1-5的整数或null>, "依据": "..."}}
], "分析说明": "<第一人称概括三要素核对依据，100字以内>"}}"""


def _reasons_by_element(fields: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in fields.get("赋值理由") or []:
        if isinstance(item, Mapping):
            element = str(item.get("要素") or "")
            if element:
                result[element] = dict(item)
    return result


def _standard_material(fields: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """构造提示词标准材料；返回 (材料文本, 章节摘要)。

    提取器已定位细化指标章节时，只发专用章节原文和“其他业务系统”通用
    赋值表；字段缺少定位结果（旧字段 JSON / 最小测试夹具）时回退标准
    全文，保持与历史行为一致。
    """
    located = fields.get("细化指标章节")
    if not isinstance(located, Mapping) or not located.get("通用章节原文"):
        return load_reference("细化指标"), {}
    generic = str(located["通用章节原文"])
    specific = located.get("专用章节原文")
    summary = {
        "匹配状态": str(located.get("匹配状态") or ""),
        "匹配依据": str(located.get("匹配依据") or ""),
        "专用章节": None,
    }
    if specific:
        summary["专用章节"] = " ".join(
            part
            for part in (located.get("专用章节号"), located.get("专用章节标题"))
            if part
        )
        material = "\n\n".join(
            [
                f"（类型定位依据：{summary['匹配依据']}）",
                "（判定要求：优先按“专用标准章节”判定；专用章节各子节均不适用、"
                "或单元属“其他/未明确”类时，按“其他业务系统”通用赋值方法判定。）",
                f"# 专用标准章节：{summary['专用章节']}\n{specific}",
                f"# 通用赋值方法：{_GENERIC_SECTION_LABEL}\n{generic}",
            ]
        )
        return material, summary
    material = "\n\n".join(
        [
            f"（类型定位依据：{summary['匹配依据']}）",
            "（判定要求：该单元未定位到专用章节，请按“其他业务系统”通用赋值方法判定。）",
            f"# 通用赋值方法：{_GENERIC_SECTION_LABEL}\n{generic}",
        ]
    )
    return material, summary


@register_rule("DJ-01", required_fields=("赋值理由",), uses_model=True)
def audit_dj_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    reasons = _reasons_by_element(fields)
    expected = ("社会影响力", "规模和服务范围", "提供服务的重要性")
    if any(element not in reasons for element in expected):
        return failed_result(
            "DJ-01",
            ["赋值理由缺少要素字段：缺少 "
             + "、".join(e for e in expected if e not in reasons)],
            "赋值理由要素不完整",
        )

    unit_type = fields.get("评测表网络类型") or "（评测表未提取到网络类型，请在标准中自行判断最接近的单元类型）"

    try:
        standard, section_summary = _standard_material(fields)
    except ReferenceDocumentMissingError as error:
        return failed_result("DJ-01", [str(error)], "无法按细化指标核对：参考标准缺失")

    prompt = _PROMPT_TEMPLATE.format(
        unit_type=unit_type,
        standard=standard,
        I=reasons["社会影响力"].get("报告赋值"),
        R=reasons["规模和服务范围"].get("报告赋值"),
        V=reasons["提供服务的重要性"].get("报告赋值"),
        reason_I=reasons["社会影响力"].get("理由原文", ""),
        reason_R=reasons["规模和服务范围"].get("理由原文", ""),
        reason_V=reasons["提供服务的重要性"].get("理由原文", ""),
    )
    try:
        reply = _chat(prompt, max_tokens=32768)
        payload = extract_json_object(reply.text)
    except (ModelNotConfiguredError, ModelCallError) as error:
        return failed_result(
            "DJ-01",
            [f"模型调用失败：{error}"],
            "无法按细化指标核对：模型调用失败",
            call_records=[call_record(prompt, error=error)],
        )

    record = [call_record(prompt, reply)]
    conclusions = {
        str(item.get("要素")): item
        for item in (payload.get("要素结论") or [])
        if isinstance(item, Mapping)
    }
    if any(element not in conclusions for element in expected):
        return failed_result(
            "DJ-01",
            ["模型输出缺少部分要素结论"],
            "无法按细化指标核对：模型结论不完整",
            call_records=record,
            评测表网络类型=fields.get("评测表网络类型"),
        )

    # 判定口径（2026-08-26 定稿）：彻底二元，判定完全跟随模型给出的具体结论，
    # 不设置信度门槛；模型给不出结论（null/缺失）也判不通过。
    detail = {
        "要素结论": None,  # 稍后填充
        "评测表网络类型": fields.get("评测表网络类型"),
    }
    if section_summary:
        detail["标准章节"] = section_summary

    issues: list[str] = []
    summary: list[str] = []
    for element in expected:
        reported = reasons[element].get("报告赋值")
        standard_value = conclusions[element].get("标准赋值")
        basis = conclusions[element].get("依据") or "—"
        summary.append(f"{element}：报告 {reported} / 标准 {standard_value}（{basis}）")
        try:
            standard_number = int(standard_value)
        except (TypeError, ValueError):
            issues.append(
                f"{element}：模型无法按细化指标判定应取赋值（{basis}）"
            )
            continue
        if standard_number != reported:
            issues.append(
                f"{element}：报告赋值 {reported}，按标准应为 {standard_number}（{basis}）"
            )

    detail["要素结论"] = summary
    if issues:
        return failed_result(
            "DJ-01", issues, "三要素赋值与细化指标标准不符", call_records=record, **detail,
        )
    return passed_result(
        "DJ-01",
        "三要素赋值均符合细化指标标准（" + "；".join(summary) + "）",
        call_records=record,
        **detail,
    )
