from __future__ import annotations

from typing import Any, Mapping

from .registry import register_rule


_REQUIRED_FIELDS = ("必填项总数", "已填写必填项数", "缺失必填项")


@register_rule("BEAN-01", required_fields=_REQUIRED_FIELDS)
def audit_bean_01(fields: Mapping[str, Any]) -> dict[str, Any]:
    if fields.get("需人工复核") is True:
        reason = str(fields.get("人工复核原因") or "备案信息表模板需要人工复核").strip()
        return {
            "item_id": "BEAN-01",
            "passed": False,
            "review_required": True,
            "conclusion": "needs_review",
            "required_count": 0,
            "filled_count": 0,
            "missing_count": 0,
            "issues": [reason],
            "message": f"模板不兼容，需人工复核：{reason}",
        }

    total = fields.get("必填项总数")
    filled = fields.get("已填写必填项数")
    missing = fields.get("缺失必填项")

    format_issues: list[str] = []
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        format_issues.append("必填项总数格式无效")
    if not isinstance(filled, int) or isinstance(filled, bool) or filled < 0:
        format_issues.append("已填写必填项数格式无效")
    elif isinstance(total, int) and not isinstance(total, bool) and filled > total:
        format_issues.append("已填写必填项数不能大于必填项总数")
    if not isinstance(missing, list):
        format_issues.append("缺失必填项格式无效")

    missing_issues: list[str] = []
    if isinstance(missing, list):
        for index, item in enumerate(missing, start=1):
            if not isinstance(item, Mapping):
                format_issues.append(f"缺失必填项第{index}项格式无效")
                continue
            path = str(item.get("字段") or "").strip()
            reason = str(item.get("原因") or "未填写").strip()
            if not path:
                format_issues.append(f"缺失必填项第{index}项没有字段名")
                continue
            raw_suffix = ""
            if "原始值" in item:
                raw_value = str(item.get("原始值") or "").strip()
                raw_suffix = f"（原值：{raw_value or '空'}）"
            missing_issues.append(f"{path}：{reason}{raw_suffix}")

    if (
        isinstance(total, int)
        and not isinstance(total, bool)
        and isinstance(filled, int)
        and not isinstance(filled, bool)
        and isinstance(missing, list)
        and filled + len(missing) != total
    ):
        format_issues.append("必填项数量统计不一致")

    issues = [*format_issues, *missing_issues]
    passed = not issues
    return {
        "item_id": "BEAN-01",
        "passed": passed,
        "review_required": False,
        "conclusion": "passed" if passed else "failed",
        "required_count": total,
        "filled_count": filled,
        "missing_count": len(missing_issues),
        "issues": issues,
        "message": (
            f"共检查{total}个必填项，均已填写"
            if passed
            else (
                "提取结果格式错误：" + "；".join(format_issues)
                if format_issues
                else f"缺失{len(missing_issues)}个必填项："
                + "；".join(missing_issues)
            )
        ),
    }
