from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .model_support import (
    StagedImageExpiredError,
    call_record,
    failed_result,
    passed_result,
    resolve_staged_image,
    topology_is_clear,
)
from .registry import register_rule
from .topology_support import (
    TopologyRecognitionError,
    extract_node_ips,
    recognize_topology,
)


def _topology_image_paths(fields: Mapping[str, Any]) -> list[Path]:
    """确定性筛图：只使用“拓扑图”文字到下一标题范围内的图片。

    优先按 id 关联；提取器拿不到 id 时按拓扑候选图片顺序对齐。
    """
    topology_images = [
        item for item in (fields.get("拓扑图") or []) if isinstance(item, Mapping)
    ]
    staged = [item for item in (fields.get("图片文件") or []) if isinstance(item, Mapping)]

    topology_ids = {
        str(image.get("id") or "")
        for image in topology_images
    }
    if topology_ids and any(topology_ids - {""}):
        paths = [
            resolve_staged_image(entry)
            for entry in staged
            if str(entry.get("id") or "") in topology_ids
        ]
        if paths:
            return paths
    if not topology_ids:
        return []
    # id 对不上但确有拓扑图：按顺序对齐兜底。
    if staged and len(staged) == len(topology_images):
        return [resolve_staged_image(entry) for entry in staged]
    return []


@register_rule(
    "FX-06",
    required_fields=("报告图片", "拓扑图", "资产IP清单"),
    uses_model=True,
)
def audit_fx_06(fields: Mapping[str, Any]) -> dict[str, Any]:
    try:
        image_paths = _topology_image_paths(fields)
    except StagedImageExpiredError as error:
        return failed_result("FX-06", [str(error)], "拓扑图图片路径已失效，无法识别")
    if not image_paths:
        return failed_result(
            "FX-06",
            ["风险评估报告中的‘拓扑图’文字到下一标题范围内未找到IMAGE标识"],
            "风险评估报告缺少网络拓扑图",
            拓扑图定位结果=[
                {
                    "定位文字": image.get("定位文字"),
                    "定位行号": image.get("定位行号"),
                    "所在章节": image.get("所在章节"),
                }
                for image in (fields.get("拓扑图") or [])
                if isinstance(image, Mapping)
            ],
        )

    try:
        payload, records = recognize_topology(image_paths)
    except TopologyRecognitionError as error:
        return failed_result(
            "FX-06",
            [f"模型调用失败：{error}"],
            "无法识别拓扑图内容：模型调用失败",
            call_records=error.records
            or [call_record(error.prompt, error=error.error)],
        )

    detail = {
        "拓扑图数量": len(image_paths),
        "模型判断拓扑图清晰": payload.get("拓扑图清晰"),
        "模型识别说明": payload.get("识别说明"),
    }

    # 判定口径（2026-09 更新）：先判清晰度，再核对 IP——
    # 模型未明确回答“拓扑图清晰”=true（不清晰或没回答）一律不通过，
    # 理由固定为“拓扑图不清晰，无法判定”；清晰时才继续 IP 核对。
    if not topology_is_clear(payload):
        return failed_result(
            "FX-06",
            [
                "拓扑图不清晰，无法判定"
                + (
                    f"（{payload.get('识别说明')}）"
                    if str(payload.get("识别说明") or "").strip()
                    else ""
                )
            ],
            "拓扑图不清晰，无法判定",
            call_records=records,
            **detail,
        )

    # IP 核对（2026-08-26 定稿）：只看 IP，不做名称匹配——
    # 没提取到任何 IP 直接不通过；提取到了由代码逐个到资产清单核对。
    ips = extract_node_ips(payload)
    asset_ips = set(fields.get("资产IP清单") or [])

    if not ips:
        return failed_result(
            "FX-06",
            [
                f"拓扑图清晰但未提取到任何 IP"
                f"（{payload.get('识别说明') or '图中无 IP 标注'}）"
            ],
            "风险评估报告拓扑图未提取到 IP，无法确认与资产清单一致",
            call_records=records,
            **detail,
        )

    detail["模型识别IP"] = ips
    extra_ips = [ip for ip in ips if ip not in asset_ips]
    if extra_ips:
        return failed_result(
            "FX-06",
            ["拓扑图中不在资产清单的 IP：" + "、".join(extra_ips)],
            "风险评估报告拓扑图与资产清单不一致",
            call_records=records,
            **detail,
        )
    return passed_result(
        "FX-06",
        f"拓扑图 {len(image_paths)} 张，提取到 {len(ips)} 个 IP，全部在资产清单中",
        call_records=records,
        **detail,
    )
