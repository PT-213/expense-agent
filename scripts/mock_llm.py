"""本地 Mock LLM：模拟 Qwen2.5-7B 的 OpenAI 兼容接口，用于无 GPU 环境验证链路。"""
from __future__ import annotations

import json
import re

from fastapi import FastAPI, Request

app = FastAPI(title="Mock Qwen2.5-7B")


def _reply(body: dict, content: str) -> dict:
    return {
        "id": "chatcmpl-mock",
        "object": "chat.completion",
        "model": body.get("model", "Qwen2.5-7B-Instruct"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 120, "total_tokens": 720},
    }


@app.get("/v1/models")
def models():
    return {"object": "list", "data": [{"id": "Qwen2.5-7B-Instruct", "object": "model"}]}


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    prompt = body["messages"][-1]["content"]

    if "档案片段：" in prompt:
        # 档案 RAG 问答：从片段里摘取最相关的一句，模拟生成式回答
        m = re.search(r"档案片段：\n(.*?)\n\n问题：", prompt, re.S)
        blocks = []
        if m:
            blocks = re.findall(r"\[(\d+)\] 《([^》]+)》[^：]*：(.+)", m.group(1), re.S)
        q = prompt.split("问题：")[-1].strip()
        noise = ("档案专用", "特此通知", "各部门", "厦门商集网络科技有限责任公司档案")
        candidates = []
        for idx, title, text in blocks[:4]:
            flat = re.sub(r"\s+", "", text)
            for sent in re.split(r"(?<=[。；])", flat):
                sent = sent.strip()
                if len(sent) < 10 or any(n in sent for n in noise):
                    continue
                score = sum(1 for ch in set(q) if ch in sent)
                if re.search(r"\d", sent):
                    score += 2
                candidates.append((score, idx, title, sent))
        if not candidates:
            return _reply(body, "未在检索到的档案中找到相关内容。")
        candidates.sort(reverse=True)
        best = candidates[:2]
        parts = [f"《{title}》中记载：{sent}[{idx}]" for _, idx, title, sent in best]
        return _reply(body, "依据档案原文：" + "；".join(parts))

    if "只返回 JSON" in prompt:
        # 只解析"事由："那一行，避免把判断标准示例误当成输入
        m = re.search(r"事由：(.+)", prompt)
        purpose = m.group(1).strip() if m else ""
        m = re.search(r"费用明细：(.+)", prompt)
        items = m.group(1).strip() if m else ""
        internal = any(k in purpose for k in ("培训", "团建", "内部"))
        bad = internal and "招待" in items
        content = json.dumps(
            {"match": not bad,
             "reason": "事由为内部活动却包含大额招待费用，关联性较弱" if bad else "费用类别与业务事由相符",
             "confidence": 0.82 if bad else 0.88},
            ensure_ascii=False,
        )
    else:
        fails = re.findall(r"^- \[(R\d+)\] ([^：]+)：(.*)$", prompt, re.M)
        hard = {"R001", "R002", "R004", "R006", "R008", "R010", "R018"}
        if any(f[0] in hard for f in fails):
            decision = "建议退回"
        elif fails:
            decision = "建议人工复核"
        else:
            decision = "建议通过"
        if fails:
            details = "；".join(f"{title}（{detail.strip()[:50]}）" for _, title, detail in fails[:2])
            content = f"{decision}。经核查，{details}。请在补充说明或调整金额后重新提交，其余校验项均无异常。"
        else:
            content = "建议通过。票据齐全、抬头一致，金额与行程校验无异常，可按申报金额报销。"

    return _reply(body, content)
