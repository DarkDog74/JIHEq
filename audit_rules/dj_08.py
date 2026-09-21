from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .model_support import (
    StagedImageExpiredError,
    call_record,
    failed_result,
    passed_result,
    resolve_staged_image,
)
from .registry import register_rule
from .topology_support import (
    TopologyRecognitionError,
    extract_node_ips,
    recognize_topology,
)


@register_rule("DJ-08", required_fields=("拓扑图", "资产IP清单"), uses_model=True)
def audit_dj_08(fields: Mapping[str, Any]) -> dict[str, Any]:
    staged = [item for item in (fields.get("图片文件") or []) if isinstance(item, Mapping)]
    if not staged:
        return failed_result(
            "DJ-08",
            ["提取阶段未记录拓扑图文件路径"],
            "拓扑图文件缺失，无法识别",
            拓扑图=fields.get("拓扑图"),
        )
    try:
        image_paths = [resolve_staged_image(entry) for entry in staged]
    except StagedImageExpiredError as error:
        return failed_result("DJ-08", [str(error)], "拓扑图图片路径已失效，无法识别")

    try:
        payload, records = recognize_topology(image_paths)
    except TopologyRecognitionError as error:
        return failed_result(
            "DJ-08",
            [f"模型调用失败：{error}"],
            "无法识别拓扑图内容：模型调用失败",
            call_records=error.records
            or [call_record(error.prompt, error=error.error)],
        )

    detail = {
        "拓扑图": fields.get("拓扑图"),
        "模型识别说明": payload.get("识别说明"),
    }

    ips = extract_node_ips(payload)
    asset_ips = set(fields.get("资产IP清单") or [])
    detail["模型识别IP"] = ips

    if not ips:
        # 识别不出 IP 即无法证明“拓扑图 IP ⊆ 资产清单”，按不通过处理
        # （与 STB-04“无图即无章”同一口径，不转人工）。
        return failed_result(
            "DJ-08",
            [
                f"模型未能从拓扑图识别出任何 IP 标注"
                f"（{payload.get('识别说明') or '图可能无 IP 标注'}）"
            ],
            "拓扑图中未能识别到任何 IP，无法确认包含在资产清单中",
            call_records=records,
            **detail,
        )

    extra_ips = [ip for ip in ips if ip not in asset_ips]
    if extra_ips:
        return failed_result(
            "DJ-08",
            ["拓扑图中不在资产清单的 IP：" + "、".join(extra_ips)],
            "定级报告拓扑图中的 IP 未全部包含在资产清单",
            call_records=records,
            **detail,
        )
    return passed_result(
        "DJ-08",
        f"拓扑图识别到 {len(ips)} 个 IP，全部在定级报告资产清单中",
        call_records=records,
        **detail,
    )
