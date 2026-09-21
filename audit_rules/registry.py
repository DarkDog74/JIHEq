from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping


AuditRuleFunction = Callable[[Mapping[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class AuditRuleSpec:
    item_id: str
    required_fields: tuple[str, ...]
    rule: AuditRuleFunction
    uses_model: bool = False


_RULES: dict[str, AuditRuleSpec] = {}


def normalize_item_id(item_id: str) -> str:
    return item_id.strip().upper().replace("_", "-")


def register_rule(
    item_id: str,
    *,
    required_fields: tuple[str, ...] = (),
    uses_model: bool = False,
) -> Callable[[AuditRuleFunction], AuditRuleFunction]:
    normalized = normalize_item_id(item_id)

    def decorator(rule: AuditRuleFunction) -> AuditRuleFunction:
        if normalized in _RULES:
            raise ValueError(f"稽核规则重复注册：{normalized}")
        _RULES[normalized] = AuditRuleSpec(
            item_id=normalized,
            required_fields=required_fields,
            uses_model=uses_model,
            rule=rule,
        )
        return rule

    return decorator


def get_rule_spec(item_id: str) -> AuditRuleSpec:
    normalized = normalize_item_id(item_id)
    try:
        return _RULES[normalized]
    except KeyError as error:
        available = "、".join(sorted(_RULES)) or "无"
        raise KeyError(f"稽核项 {normalized} 没有规则；可用稽核项：{available}") from error


def list_rule_items() -> list[str]:
    return sorted(_RULES)
