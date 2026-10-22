"""语义校验规则：把 LLM 判断包装成标准 CheckResult，与其他规则同一套输出。"""
from __future__ import annotations

from . import llm
from .rules import CheckResult


def rule_semantic_match(claim, docs, items, ctx):
    """R020：报销事由与费用明细语义匹配（LLM）。

    未配置模型时直接跳过（返回空列表，不产生"通过"占位，避免误导）。
    """
    if not llm.available():
        return []
    if not items:
        return []

    result = llm.semantic_check(claim, items)
    if not result:
        return []

    if result["match"]:
        return [CheckResult(
            "R020", "语义匹配", "pass", "low",
            "报销事由与费用明细匹配",
            result["reason"] or "未发现明显不相关",
            f"事由：{claim.get('purpose') or '—'}",
            "",
            source="llm",
            confidence=result["confidence"],
        )]

    return [CheckResult(
        "R020", "语义匹配", "warn", "medium",
        "报销事由与费用明细可能不匹配",
        result["reason"] or "模型判断事由与费用类别关联性较弱",
        f"事由：{claim.get('purpose') or '—'}；明细："
        + "、".join(f"{i['category']}{float(i['amount']):.2f}" for i in items[:4]),
        "请确认费用是否与本次业务相关",
        source="llm",
        confidence=result["confidence"],
    )]
