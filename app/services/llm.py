"""LLM 接入层：OpenAI 兼容协议，支持 vLLM / Ollama / 百炼 / DeepSeek。

设计原则
- 模型只负责"表达"和"语义判断"，不参与金额、日期等硬规则决策；
- 任何调用失败都返回降级标记，不阻塞主流程；
- 超时、并发、重试由调用方控制，这里只做单次请求。
"""
from __future__ import annotations

import json
import re

import httpx

from .. import config


class LLMError(Exception):
    """模型调用失败（超时、网络、返回格式错误等）。"""


def available() -> bool:
    return config.LLM_ENABLED


def model_label() -> str:
    if not config.LLM_ENABLED:
        return "未启用"
    return config.LLM_MODEL or "LLM"


def chat(messages: list[dict], *, temperature: float = 0.1, max_tokens: int = 400,
         timeout: float = 30.0) -> str:
    """单次对话调用，失败抛 LLMError。"""
    if not config.LLM_ENABLED:
        raise LLMError("LLM 未配置")
    try:
        resp = httpx.post(
            f"{config.LLM_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.LLM_API_KEY}"},
            json={
                "model": config.LLM_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except httpx.TimeoutException as exc:
        raise LLMError(f"LLM 调用超时（{timeout}s）") from exc
    except Exception as exc:
        raise LLMError(str(exc)[:200]) from exc


def generate_review_comment(claim: dict, checks: list, items: list) -> tuple[str, str]:
    """规则跑完后生成自然语言审核意见。

    返回 (文本, 来源)，来源取值：
      - llm       模型成功生成
      - fallback  模型调用失败，调用方应使用模板
      - disabled  未配置模型
    """
    if not config.LLM_ENABLED:
        return "", "disabled"

    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    rules_text = "\n".join(
        f"- [{c.code}] {c.title}：{c.detail or c.name}" for c in (fails + warns)
    ) or "- 无阻断项、无提示项"

    items_text = "\n".join(
        f"- {i['category']} {float(i['amount']):.2f} 元"
        + (f"（{i.get('merchant')}）" if i.get("merchant") else "")
        for i in items
    ) or "- 无明细"

    prompt = f"""你是企业财务共享中心的审核专员。请根据以下已完成的规则校验结果，写一段 80-150 字的中文审核意见。

【报销单】
事由：{claim.get('purpose') or '—'}
行程：{claim.get('trip_start') or '—'} 至 {claim.get('trip_end') or '—'}
目的地：{claim.get('destination') or '—'}
申报金额：{float(claim.get('claimed_amount') or 0):.2f} 元

【费用明细】
{items_text}

【规则校验结果】
{rules_text}

要求：
1. 结论先行：明确说“建议通过”“建议人工复核”或“建议退回”。
2. 只能引用上面列出的规则，不得编造不存在的问题，不得修改金额与日期判定。
3. 如涉及金额调整，说明可报金额及依据。
4. 语气专业、简洁，像真人财务写的评语。
5. 直接输出意见正文，不要加标题或解释。"""

    try:
        text = chat([{"role": "user", "content": prompt}], max_tokens=350, timeout=30)
        return text[:800], "llm"
    except LLMError:
        return "", "fallback"


def semantic_check(claim: dict, items: list) -> dict | None:
    """LLM 语义规则：报销事由与费用明细是否匹配。

    返回 {"match": bool, "reason": str, "confidence": float} 或 None（不可用/解析失败）。
    """
    if not config.LLM_ENABLED or not items:
        return None

    items_text = "；".join(f"{i['category']} {float(i['amount']):.2f} 元" for i in items)
    prompt = f"""判断以下报销事由与费用明细是否匹配。

事由：{claim.get('purpose') or '—'}
目的地：{claim.get('destination') or '—'}
费用明细：{items_text}

只返回 JSON，不要任何解释：
{{"match": true 或 false, "reason": "一句话理由（不超过40字）", "confidence": 0到1之间的小数}}

判断标准：
- 事由提到的业务活动与费用类别是否合理相关（如“客户拜访”含住宿交通合理；“内部培训”含大额招待不合理）
- 如果事由过于笼统、无法判断，返回 match=true，confidence 给 0.5 左右"""

    try:
        text = chat([{"role": "user", "content": prompt}], max_tokens=120, timeout=20)
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            return None
        data = json.loads(m.group(0))
        return {
            "match": bool(data.get("match", True)),
            "reason": str(data.get("reason", ""))[:80],
            "confidence": max(0.0, min(1.0, float(data.get("confidence", 0.7)))),
        }
    except Exception:
        return None
