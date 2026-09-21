"""模型稽核规则共用工具：结果构造、调用留档、参考标准读取。

判定口径（2026-08-26 用户定稿）：在用规则一律彻底二元——判定完全跟随模型
给出的具体结论，不设置信度门槛（提示词也不再要求输出置信度）；模型故障、
给不出结论一律判不通过；留痕机制保留（提示词全文/原始输出/耗时随结果留档）。
confidence_of / confidence_too_low / review_result 仅供停用项 ZM-01 沿用。
"""

from __future__ import annotations

import glob
import re
from pathlib import Path
from typing import Any, Mapping

from core.model_client import ModelCallError, ModelReply, extract_json_object

CONFIDENCE_THRESHOLD = 0.6
REFERENCE_ROOT = Path(__file__).resolve().parents[1] / "Reference_documents"

_REFERENCE_FILES = {
    "细化指标": "网络和系统定级要素赋值细化指标.md",
    "系统类型划分": "系统类型划分.json",
    "GBT": "GBT+20984-2022.md",
}


class ReferenceDocumentMissingError(RuntimeError):
    """Reference_documents 下缺少参考标准文件。"""


class StagedImageExpiredError(ModelCallError):
    """staged 图片绝对路径与 audits 相对路径均无法定位到文件。"""


def resolve_staged_image(entry: Mapping[str, Any]) -> Path:
    """解析图片文件条目为存在的路径。

    提取阶段记录的是任务运行时的 staging 绝对路径；任务发布（staging 改名
    为最终目录）后该路径失效，此时按 audits 相对路径在全部系统目录下回溯。
    相对路径逐段 glob.escape，避免文件名含 [ ] * ? 被当作通配符误匹配。
    """
    absolute = Path(str(entry.get("路径") or ""))
    if absolute.is_file():
        return absolute

    relative = str(entry.get("相对路径") or "")
    if not relative and "audits" in absolute.parts:
        index = absolute.parts.index("audits")
        relative = "/".join(absolute.parts[index:])
    if not relative:
        return absolute

    from platform_app.config import OUTPUT_ROOT

    pattern = "/".join(glob.escape(part) for part in relative.split("/"))
    for candidate in sorted(OUTPUT_ROOT.glob(f"*/{pattern}")):
        if candidate.is_file():
            return candidate
    raise StagedImageExpiredError(
        f"staged 图片路径已失效：{relative}"
        f"（原始路径 {absolute}，结果目录可能已被新任务替换）"
    )


def load_reference(name: str) -> str:
    filename = _REFERENCE_FILES[name]
    path = REFERENCE_ROOT / filename
    if not path.is_file():
        raise ReferenceDocumentMissingError(f"参考标准缺失：Reference_documents/{filename}")
    return path.read_text(encoding="utf-8-sig")


def call_record(
    prompt: str,
    reply: ModelReply | None = None,
    error: Exception | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {"提示词": prompt}
    if reply is not None:
        record.update(
            {
                "模型": reply.model,
                "原始输出": reply.text,
                "耗时ms": reply.elapsed_ms,
            }
        )
    if error is not None:
        record["错误"] = f"{type(error).__name__}: {error}"
    return record


def _analysis_basis(payload: Mapping[str, Any]) -> str:
    for key in ("分析说明", "依据", "判断依据", "分类依据", "匹配依据", "识别说明"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:260]
    conclusions = payload.get("要素结论")
    if isinstance(conclusions, list):
        parts = []
        for item in conclusions[:3]:
            if not isinstance(item, Mapping):
                continue
            label = str(item.get("要素") or "").strip()
            basis = str(item.get("依据") or "").strip()
            if label or basis:
                parts.append(f"{label}：{basis}".strip("："))
        if parts:
            return "；".join(parts)[:260]
    return ""


def _clean_narration_text(text: str) -> str:
    cleaned = " ".join(str(text or "").split()).strip("。；; ")
    for forbidden, replacement in (
        ("AI分析结果", "分析结果"),
        ("模型", "识别"),
        ("程序", "本次核对"),
        ("规则", "要求"),
        ("预设结论", "结论"),
        ("固定结论", "结论"),
    ):
        cleaned = cleaned.replace(forbidden, replacement)
    return cleaned


def _precomputed_narration(
    call_records: list[dict[str, Any]], *, passed: bool, message: str
) -> dict[str, Any]:
    """把本项唯一一次语义识别直接转成页面说明，避免再次调用包装模型。"""
    successful = [record for record in call_records if record.get("原始输出")]
    model = next(
        (str(record.get("模型")) for record in reversed(successful) if record.get("模型")),
        "glm-5v-turbo",
    )
    if not successful:
        error = next(
            (str(record.get("错误")) for record in reversed(call_records) if record.get("错误")),
            "语义识别未完成",
        )
        return {
            "attempted": True,
            "status": "failed",
            "model": model,
            "text": f"说明生成失败：{_clean_narration_text(error)}",
            "error_code": "precomputed_analysis_failed",
            "usage": {},
        }

    raw = str(successful[-1].get("原始输出") or "")
    try:
        payload = extract_json_object(raw)
    except ModelCallError:
        payload = {}
    basis = _clean_narration_text(_analysis_basis(payload))
    conclusion = _clean_narration_text(message)
    pieces = ["我核对了本项提取内容"]
    if basis:
        pieces.append(basis)
    if conclusion and conclusion not in basis:
        pieces.append(conclusion)
    final_status = "通过" if passed else "不通过"
    text = "。".join(piece for piece in pieces if piece) + f"。因此，本项判断为{final_status}。"
    return {
        "attempted": True,
        "status": "completed",
        "model": model,
        "text": text,
        "usage": {},
    }


def confidence_of(payload: Mapping[str, Any]) -> float | None:
    """读取模型输出的“置信度”字段；缺失或非法返回 None（调用方应转人工）。"""
    value = payload.get("置信度")
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = re.search(r"\d+(?:\.\d+)?", value)
        if match:
            return float(match.group())
    return None


def confidence_too_low(payload: Mapping[str, Any]) -> bool:
    confidence = confidence_of(payload)
    return confidence is None or confidence < CONFIDENCE_THRESHOLD


def passed_result(
    item_id: str, message: str, *, call_records: list[dict[str, Any]] | None = None, **extra: Any
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "item_id": item_id,
        "passed": True,
        "issues": [],
        "message": message,
    }
    result.update(extra)
    if call_records:
        result["模型调用记录"] = call_records
        result["_precomputed_narration"] = _precomputed_narration(
            call_records, passed=True, message=message
        )
    return result


def failed_result(
    item_id: str,
    issues: list[str],
    message: str,
    *,
    review_required: bool = False,
    call_records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "item_id": item_id,
        "passed": False,
        "issues": issues,
        "message": message,
    }
    if review_required:
        result["review_required"] = True
    result.update(extra)
    if call_records:
        result["模型调用记录"] = call_records
        result["_precomputed_narration"] = _precomputed_narration(
            call_records, passed=False, message=message
        )
    return result


def review_result(
    item_id: str,
    reason: str,
    *,
    call_records: list[dict[str, Any]] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """模型不可用 / 置信度不足 / 输出异常时的人工复核结果。"""
    return failed_result(
        item_id,
        [reason],
        f"需人工复核：{reason}",
        review_required=True,
        call_records=call_records,
        **extra,
    )


def not_applicable_result(item_id: str, message: str) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "passed": True,
        "applicable": False,
        "issues": [],
        "message": message,
    }


TOPOLOGY_PROMPT_TEMPLATE = (
    "这是某通信系统的网络拓扑图（共 {count} 张，按顺序给出）。请逐张识别图中的全部内容。\n"
    "只输出一个 JSON 对象，不要输出任何其他文字，结构如下：\n"
    '{"节点": [{"名称": "<图中文字>", "IP": "<图中标注IP，没有则null>", '
    '"类型": "<防火墙/交换机/服务器/安全设备/未知>"}], '
    '"识别说明": "<清晰度与看不清的内容，30字以内>", '
    '"分析说明": "<第一人称概括识别证据，80字以内>"}\n'
    "注意：逐字抄录图中标注，不要推测补全；看不清的内容写进\"识别说明\"，不要编造，不要展开描述。"
)


def topology_prompt(image_count: int) -> str:
    return TOPOLOGY_PROMPT_TEMPLATE.replace("{count}", str(image_count))


def extract_node_ips(payload: Mapping[str, Any]) -> list[str]:
    """从拓扑提取结果中取合法 IP（硬校验：格式不符一律丢弃）。"""
    valid = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")
    ips: list[str] = []
    for node in payload.get("节点") or []:
        if not isinstance(node, Mapping):
            continue
        ip = str(node.get("IP") or "").strip()
        if ip and valid.match(ip) and ip not in ips:
            ips.append(ip)
    return ips


def extract_node_names(payload: Mapping[str, Any]) -> list[str]:
    names: list[str] = []
    for node in payload.get("节点") or []:
        if not isinstance(node, Mapping):
            continue
        name = str(node.get("名称") or "").strip()
        if name and name not in names:
            names.append(name)
    return names
