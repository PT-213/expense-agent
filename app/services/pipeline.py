"""数字员工执行引擎：感知 → 抽取 → 校验 → 决策 → 待办。"""
from __future__ import annotations

import json
import threading
import traceback
from pathlib import Path

from ..db import db, now_iso
from . import agent as agent_service
from . import invoice as invoice_service
from . import llm as llm_service
from . import rules as rule_engine

CATEGORY_MAP = {
    "air": "交通", "train": "交通", "taxi": "市内交通",
    "hotel": "住宿", "meal": "餐饮", "entertainment": "业务招待",
    "invoice": "其他",
}

_running: set[int] = set()
_lock = threading.Lock()


def next_claim_no(conn) -> str:
    prefix = "BX" + now_iso()[:7].replace("-", "")
    count = conn.execute("SELECT COUNT(*) c FROM claims WHERE claim_no LIKE ?", (prefix + "%",)).fetchone()["c"]
    return f"{prefix}-{count + 1:04d}"


def append_trace(conn, task_id: int, stage: str, detail: str, status: str = "running",
                 progress: int | None = None) -> None:
    row = conn.execute("SELECT trace FROM tasks WHERE id=?", (task_id,)).fetchone()
    trace = []
    if row and row["trace"]:
        try:
            trace = json.loads(row["trace"])
        except Exception:
            trace = []
    trace.append({"stage": stage, "detail": detail, "at": now_iso(), "status": status})
    if progress is None:
        conn.execute("UPDATE tasks SET trace=?, stage=?, message=? WHERE id=?",
                     (json.dumps(trace, ensure_ascii=False), stage, detail, task_id))
    else:
        conn.execute("UPDATE tasks SET trace=?, stage=?, message=?, progress=? WHERE id=?",
                     (json.dumps(trace, ensure_ascii=False), stage, detail, progress, task_id))


def _category(kind: str) -> str:
    return CATEGORY_MAP.get(kind, "其他")


def _occurred_date(fields: dict, fallback: str) -> str:
    for key in ("invoice_date", "check_in"):
        if fields.get(key):
            return str(fields[key])[:10]
    return fallback


def execute_claim(claim_id: int, task_id: int | None = None) -> dict:
    """同步执行完整预审，返回决策摘要。"""
    with db() as conn:
        claim = dict(conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone())
        docs = []
        for row in conn.execute("SELECT * FROM documents WHERE claim_id=? ORDER BY id", (claim_id,)):
            d = dict(row)
            try:
                d["fields"] = json.loads(row["fields"] or "{}")
            except Exception:
                d["fields"] = {}
            docs.append(d)
        if task_id:
            conn.execute("UPDATE tasks SET status='running', started_at=?, stage='读取单据', progress=8 WHERE id=?",
                         (now_iso(), task_id))
            append_trace(conn, task_id, "读取单据",
                         f"加载报销单 {claim['claim_no']} 与 {len(docs)} 份票据", progress=8)

    cm = db()
    conn = cm.__enter__()
    try:
        conn.execute("DELETE FROM line_items WHERE claim_id=?", (claim_id,))
        items: list[dict] = []
        for d in docs:
            f = d["fields"]
            if not f:
                continue
            amount = float(f.get("total") or f.get("amount") or 0)
            if amount <= 0:
                continue
            item = {
                "doc_id": d["id"], "category": _category(f.get("kind") or d["kind"]),
                "occurred_on": _occurred_date(f, claim.get("trip_start") or ""),
                "city": f.get("city") or claim.get("destination") or "",
                "merchant": f.get("seller") or "",
                "invoice_no": f.get("invoice_no") or "",
                "amount": amount, "tax_amount": float(f.get("tax") or 0),
                "description": "、".join(f.get("items") or [])[:120],
                "confidence": float(d.get("confidence") or 1),
                "nights": int(f.get("nights") or 1),
            }
            cur = conn.execute(
                "INSERT INTO line_items(claim_id, doc_id, category, occurred_on, city, merchant,"
                " invoice_no, amount, tax_amount, description, confidence, source, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, item["doc_id"], item["category"], item["occurred_on"], item["city"],
                 item["merchant"], item["invoice_no"], item["amount"], item["tax_amount"],
                 item["description"], item["confidence"], "ai", now_iso()),
            )
            item["id"] = cur.lastrowid
            items.append(item)

        if task_id:
            append_trace(conn, task_id, "票据抽取",
                         f"识别 {len(items)} 条费用明细：" + "、".join(
                             f"{i['category']}{i['amount']:.2f}" for i in items[:4]), progress=35)

        # 多 Agent 编排：主控 Planner → 工具 Agent(规则/政策/风控) → 复核 Agent
        # 决策仍由确定性规则给出，模型只做规划、检索与表达；trace 由各 Agent 节点写入。
        result = agent_service.run_review(claim, docs, items, conn, task_id, append_trace)
        checks = result["checks"]
        decision = result["decision"]
        ai_source = result["ai_source"]

        conn.execute("DELETE FROM checks WHERE claim_id=?", (claim_id,))
        policy_rows = {r["code"]: r["id"] for r in conn.execute("SELECT id, code FROM policies")}
        for c in checks:
            conn.execute(
                "INSERT INTO checks(claim_id, rule_code, rule_name, policy_id, severity, status,"
                " title, detail, evidence, suggestion, source, confidence, created_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_id, c.code, c.name, policy_rows.get(c.policy_code), c.severity, c.status,
                 c.title, c.detail, c.evidence, c.suggestion, c.source, c.confidence, now_iso()),
            )

        status = {"pass": "auto_passed", "review": "pending_review",
                  "reject": "pending_review"}[decision["decision"]]
        conn.execute(
            "UPDATE claims SET status=?, ai_decision=?, ai_confidence=?, ai_summary=?,"
            " ai_source=?, risk_level=?, approved_amount=?, updated_at=? WHERE id=?",
            (status, decision["decision"], decision["confidence"], decision["summary"],
             ai_source, decision["risk_level"], decision["approved_amount"], now_iso(), claim_id),
        )
        if task_id:
            append_trace(conn, task_id, "完成",
                         "已生成预审意见" + ("，自动通过" if status == "auto_passed" else "，等待人工审批"),
                         status="done", progress=100)
            conn.execute("UPDATE tasks SET status='done', stage='完成', finished_at=? WHERE id=?",
                         (now_iso(), task_id))
        conn.commit()
        return decision
    except Exception as exc:
        conn.rollback()
        with db() as c2:
            c2.execute("UPDATE claims SET status='failed', updated_at=? WHERE id=?", (now_iso(), claim_id))
            if task_id:
                c2.execute("UPDATE tasks SET status='failed', stage='失败', error=?, finished_at=? WHERE id=?",
                           (traceback.format_exc(limit=3), now_iso(), task_id))
                append_trace(c2, task_id, "失败", str(exc)[:200], status="failed")
        raise
    finally:
        try:
            cm.__exit__(None, None, None)
        except Exception:
            pass


def enqueue(claim_id: int, user_id: int | None, use_thread: bool = True) -> int:
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO tasks(claim_id, status, stage, progress, message, trace, created_at)"
            " VALUES (?, 'queued', '排队中', 0, '等待数字员工领取', '[]', ?)",
            (claim_id, now_iso()),
        )
        task_id = cur.lastrowid
    if use_thread:
        threading.Thread(target=_run, args=(task_id, claim_id, user_id), daemon=True).start()
    return task_id


def _run(task_id: int, claim_id: int, user_id: int | None) -> None:
    with _lock:
        if task_id in _running:
            return
        _running.add(task_id)
    try:
        execute_claim(claim_id, task_id)
    except Exception:
        pass
    finally:
        with _lock:
            _running.discard(task_id)


def parse_and_store(claim_id: int, path: Path, original_name: str) -> int:
    parsed = invoice_service.parse_pdf(path, original_name)
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO documents(claim_id, kind, original_name, stored_name, image_path, text,"
            " method, confidence, fields, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (claim_id, parsed.kind, original_name, path.name, parsed.image_path, parsed.text,
             parsed.method, parsed.confidence, json.dumps(parsed.fields, ensure_ascii=False), now_iso()),
        )
        return cur.lastrowid


