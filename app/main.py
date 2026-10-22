"""报销预审数字员工 · API 与页面。"""
from __future__ import annotations

import json
import shutil
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config
from .db import audit, db, init_db, now_iso
from .security import (
    can_view_claim, client_ip, create_session, drop_session, get_user, hash_password,
    require_role, require_user, verify_password,
)
from .services import pipeline, rules
from .services.policies import search_policies
from .services.seed import seed_all

STATIC = Path(__file__).parent / "static"


def _startup() -> None:
    init_db()
    if config.SEED_ON_START:
        seed_all()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _startup()
    yield


app = FastAPI(title="报销预审数字员工", version="1.0.0", docs_url="/api/docs", lifespan=lifespan)


@app.get("/health")
def health():
    with db() as conn:
        claims = conn.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]
        pending = conn.execute(
            "SELECT COUNT(*) c FROM claims WHERE status IN ('pending_review','submitted','processing')"
        ).fetchone()["c"]
    return {"status": "ok", "claims": claims, "pending": pending,
            "llm": config.LLM_ENABLED, "model": config.LLM_MODEL if config.LLM_ENABLED else ""}


# ---------- 页面 ----------

@app.get("/")
def index(request: Request):
    if not get_user(request):
        return RedirectResponse("/login")
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/login")
def login_page():
    return FileResponse(STATIC / "login.html", headers={"Cache-Control": "no-store"})


# ---------- 认证 ----------

@app.post("/api/auth/login")
async def login(payload: dict, request: Request):
    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            return JSONResponse({"detail": "用户名或密码错误"}, status_code=401)
        token = create_session(conn, row["id"])
        audit(conn, row, "登录", "session", "", "登录成功", client_ip(request))
    resp = JSONResponse({"ok": True, "user": {
        "username": row["username"], "display_name": row["display_name"], "role": row["role"]}})
    resp.set_cookie("expense_session", token, httponly=True, samesite="lax",
                    max_age=60 * 60 * 24 * config.SESSION_DAYS)
    return resp


@app.post("/api/auth/logout")
def logout(request: Request, user=Depends(require_user)):
    token = request.cookies.get("expense_session", "")
    with db() as conn:
        drop_session(conn, token)
        audit(conn, user, "退出", "session", "", "", client_ip(request))
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("expense_session")
    return resp


@app.get("/api/me")
def me(user=Depends(require_user)):
    return {
        "username": user["username"], "display_name": user["display_name"],
        "role": user["role"], "role_label": config.ROLE_LABELS.get(user["role"], user["role"]),
        "department": user["department"], "llm": config.LLM_ENABLED,
        "llm_model": config.LLM_MODEL if config.LLM_ENABLED else "",
        "auto_pass_threshold": config.AUTO_PASS_CONFIDENCE,
    }


# ---------- 概览 ----------

@app.get("/api/stats")
def stats(user=Depends(require_user)):
    scope = "" if user["role"] in ("admin", "approver") else " AND applicant_id=?"
    params = [] if user["role"] in ("admin", "approver") else [user["id"]]
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM claims WHERE 1=1{scope}", params).fetchone()["c"]
        pending = conn.execute(
            f"SELECT COUNT(*) c FROM claims WHERE status IN ('pending_review','submitted','processing'){scope}",
            params).fetchone()["c"]
        auto = conn.execute(f"SELECT COUNT(*) c FROM claims WHERE status='auto_passed'{scope}", params).fetchone()["c"]
        approved = conn.execute(f"SELECT COUNT(*) c FROM claims WHERE status='approved'{scope}", params).fetchone()["c"]
        rejected = conn.execute(f"SELECT COUNT(*) c FROM claims WHERE status='rejected'{scope}", params).fetchone()["c"]
        amount = conn.execute(
            f"SELECT COALESCE(SUM(approved_amount),0) s FROM claims WHERE status IN ('approved','auto_passed'){scope}",
            params).fetchone()["s"]
        auto_amount = conn.execute(
            f"SELECT COALESCE(SUM(claimed_amount),0) s FROM claims WHERE status='auto_passed'{scope}",
            params).fetchone()["s"]
        checks = conn.execute(
            f"""SELECT c.status, COUNT(*) n FROM checks c JOIN claims cl ON cl.id=c.claim_id
                WHERE 1=1{scope.replace('applicant_id', 'cl.applicant_id')} GROUP BY c.status""",
            params).fetchall()
        top_rules = conn.execute(
            f"""SELECT c.rule_name, c.status, COUNT(*) n FROM checks c JOIN claims cl ON cl.id=c.claim_id
                WHERE c.status<>'pass'{scope.replace('applicant_id', 'cl.applicant_id')}
                GROUP BY c.rule_name, c.status ORDER BY n DESC LIMIT 6""",
            params).fetchall()
        recent = conn.execute(
            f"""SELECT id, claim_no, applicant_name, purpose, destination, claimed_amount,
                       approved_amount, status, ai_decision, ai_confidence, risk_level, updated_at
                FROM claims WHERE 1=1{scope} ORDER BY updated_at DESC LIMIT 6""", params).fetchall()
        daily = conn.execute(
            f"""SELECT substr(updated_at,1,10) d, COUNT(*) n FROM claims
                WHERE 1=1{scope} GROUP BY d ORDER BY d DESC LIMIT 7""", params).fetchall()

    check_map = {r["status"]: r["n"] for r in checks}
    handled = auto + approved
    return {
        "total": total, "pending": pending, "auto_passed": auto, "approved": approved,
        "rejected": rejected, "handled": handled,
        "auto_rate": round(auto / handled * 100, 1) if handled else 0,
        "approved_amount": round(amount, 2), "auto_amount": round(auto_amount, 2),
        "checks": {"pass": check_map.get("pass", 0), "warn": check_map.get("warn", 0),
                   "fail": check_map.get("fail", 0)},
        "top_rules": [dict(r) for r in top_rules],
        "recent": [dict(r) for r in recent],
        "trend": [dict(r) for r in reversed(daily)],
    }


# ---------- 报销单 ----------

@app.get("/api/claims")
def list_claims(status: str = "", q: str = "", page: int = 1, size: int = 12,
                user=Depends(require_user)):
    where = ["1=1"]
    params: list = []
    if user["role"] not in ("admin", "approver"):
        where.append("c.applicant_id=?")
        params.append(user["id"])
    if status:
        where.append("c.status=?")
        params.append(status)
    if q:
        where.append("(c.claim_no LIKE ? OR c.purpose LIKE ? OR c.applicant_name LIKE ?)")
        params += [f"%{q}%"] * 3
    clause = " AND ".join(where)
    page = max(page, 1)
    size = min(max(size, 1), 50)
    with db() as conn:
        total = conn.execute(f"SELECT COUNT(*) c FROM claims c WHERE {clause}", params).fetchone()["c"]
        rows = conn.execute(
            f"""SELECT c.*, t.id task_id, t.status task_status, t.progress task_progress,
                       (SELECT COUNT(*) FROM documents d WHERE d.claim_id=c.id) files,
                       (SELECT COUNT(*) FROM checks k WHERE k.claim_id=c.id AND k.status='fail') fails,
                       (SELECT COUNT(*) FROM checks k WHERE k.claim_id=c.id AND k.status='warn') warns
                FROM claims c LEFT JOIN tasks t ON t.claim_id=c.id
                WHERE {clause} ORDER BY c.updated_at DESC LIMIT ? OFFSET ?""",
            [*params, size, (page - 1) * size],
        ).fetchall()
    return {"total": total, "page": page, "size": size, "items": [dict(r) for r in rows]}


@app.get("/api/claims/{claim_id}")
def claim_detail(claim_id: int, user=Depends(require_user)):
    with db() as conn:
        claim = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim:
            raise HTTPException(404, "报销单不存在")
        if not can_view_claim(user, claim):
            raise HTTPException(403, "无权查看该报销单")
        docs = []
        for row in conn.execute("SELECT * FROM documents WHERE claim_id=? ORDER BY id", (claim_id,)):
            d = dict(row)
            try:
                d["fields"] = json.loads(row["fields"] or "{}")
            except Exception:
                d["fields"] = {}
            docs.append(d)
        items = [dict(r) for r in conn.execute(
            "SELECT * FROM line_items WHERE claim_id=? ORDER BY occurred_on, id", (claim_id,))]
        checks = [dict(r) for r in conn.execute(
            """SELECT k.*, p.code AS policy_code, p.title AS policy_title,
                      p.content AS policy_content
               FROM checks k LEFT JOIN policies p ON p.id = k.policy_id
               WHERE k.claim_id=?
               ORDER BY CASE k.status WHEN 'fail' THEN 0 WHEN 'warn' THEN 1 ELSE 2 END, k.id""",
            (claim_id,))]
        for chk in checks:
            if chk.get("policy_content"):
                import re as _re
                m = _re.search(r"(第[一二三四五六七八九十]+条[^\n]*)", chk["policy_content"])
                chk["policy_clause"] = m.group(1) if m else chk["policy_content"][:200]
        task = conn.execute("SELECT * FROM tasks WHERE claim_id=? ORDER BY id DESC LIMIT 1", (claim_id,)).fetchone()
        approvals = [dict(r) for r in conn.execute(
            "SELECT * FROM approvals WHERE claim_id=? ORDER BY id DESC", (claim_id,))]
        feedback = [dict(r) for r in conn.execute(
            "SELECT * FROM feedback WHERE claim_id=? ORDER BY id DESC", (claim_id,))]
        audit(conn, user, "查阅", "claim", claim_id, claim["claim_no"], "")
    data = dict(claim)
    data["documents"] = docs
    data["items"] = items
    data["checks"] = checks
    if task:
        t = dict(task)
        try:
            t["trace"] = json.loads(t.get("trace") or "[]")
        except Exception:
            t["trace"] = []
        data["task"] = t
    else:
        data["task"] = None
    data["approvals"] = approvals
    data["feedback"] = feedback
    data["auto_pass_threshold"] = config.AUTO_PASS_CONFIDENCE
    data["can_decide"] = user["role"] in ("admin", "approver")
    return data


@app.post("/api/claims")
async def create_claim(request: Request,
                       purpose: str = Form(""),
                       trip_start: str = Form(""),
                       trip_end: str = Form(""),
                       destination: str = Form(""),
                       category: str = Form("差旅费"),
                       claimed_amount: float = Form(0),
                       files: list[UploadFile] = File(default=[]),
                       user=Depends(require_role("admin", "approver", "employee"))):
    if not purpose.strip():
        raise HTTPException(400, "请填写报销事由")
    if not files:
        raise HTTPException(400, "请至少上传一份票据")

    with db() as conn:
        claim_no = pipeline.next_claim_no(conn)
        cur = conn.execute(
            "INSERT INTO claims(claim_no, applicant_id, applicant_name, department, purpose,"
            " trip_start, trip_end, destination, category, claimed_amount, status,"
            " created_at, submitted_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (claim_no, user["id"], user["display_name"], user["department"], purpose.strip(),
             trip_start, trip_end, destination, category, float(claimed_amount), "processing",
             now_iso(), now_iso(), now_iso()),
        )
        claim_id = cur.lastrowid
        audit(conn, user, "提交报销", "claim", claim_id, claim_no, client_ip(request))

    stored_files = []
    for item in files:
        if not item.filename:
            continue
        if Path(item.filename).suffix.lower() not in (".pdf",):
            continue
        raw = await item.read()
        if len(raw) > config.MAX_UPLOAD_MB * 1024 * 1024:
            continue
        stored = config.UPLOAD_DIR / f"{uuid.uuid4().hex}_{Path(item.filename).name}"
        stored.write_bytes(raw)
        stored_files.append((stored, Path(item.filename).name))

    for stored, name in stored_files:
        try:
            pipeline.parse_and_store(claim_id, stored, name)
        except Exception as exc:
            with db() as conn:
                conn.execute("UPDATE claims SET status='failed', updated_at=? WHERE id=?",
                             (now_iso(), claim_id))
            raise HTTPException(400, f"票据解析失败：{name} - {exc}")

    task_id = pipeline.enqueue(claim_id, user["id"])
    return {"ok": True, "claim_id": claim_id, "claim_no": claim_no, "task_id": task_id}


@app.post("/api/claims/{claim_id}/rerun")
def rerun(claim_id: int, user=Depends(require_role("admin", "approver"))):
    with db() as conn:
        claim = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim:
            raise HTTPException(404, "报销单不存在")
        conn.execute("UPDATE claims SET status='processing', updated_at=? WHERE id=?",
                     (now_iso(), claim_id))
        audit(conn, user, "重新预审", "claim", claim_id, claim["claim_no"])
    task_id = pipeline.enqueue(claim_id, user["id"])
    return {"ok": True, "task_id": task_id}


@app.post("/api/claims/{claim_id}/decision")
async def decide(claim_id: int, payload: dict, request: Request,
                 user=Depends(require_role("admin", "approver"))):
    action = str(payload.get("action", "")).strip()
    if action not in ("approve", "reject"):
        raise HTTPException(400, "action 只能是 approve 或 reject")
    comment = str(payload.get("comment", "")).strip()
    approved_amount = payload.get("approved_amount")
    corrections = payload.get("corrections") or []

    with db() as conn:
        claim = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim:
            raise HTTPException(404, "报销单不存在")
        if claim["status"] in ("approved", "rejected"):
            raise HTTPException(400, "该报销单已处理，不能重复审批")
        amount = float(approved_amount) if approved_amount is not None else float(claim["approved_amount"] or claim["claimed_amount"])
        if action == "reject":
            amount = 0.0
        status = "approved" if action == "approve" else "rejected"
        conn.execute(
            "UPDATE claims SET status=?, approved_amount=?, decided_at=?, updated_at=? WHERE id=?",
            (status, amount, now_iso(), now_iso(), claim_id),
        )
        conn.execute(
            "INSERT INTO approvals(claim_id, approver_id, approver_name, action, comment,"
            " ai_decision, approved_amount, corrections, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (claim_id, user["id"], user["display_name"], action, comment, claim["ai_decision"],
             amount, json.dumps(corrections, ensure_ascii=False), now_iso()),
        )
        for fix in corrections:
            conn.execute(
                "INSERT INTO feedback(claim_id, check_id, rule_code, ai_label, human_label, note, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (claim_id, fix.get("check_id"), fix.get("rule_code", ""),
                 fix.get("ai_label", ""), fix.get("human_label", ""), fix.get("note", ""), now_iso()),
            )
        audit(conn, user, "审批通过" if action == "approve" else "审批驳回",
              "claim", claim_id, f"{claim['claim_no']} · {comment[:80]}", client_ip(request))

    return {"ok": True, "status": status, "approved_amount": round(amount, 2)}


@app.get("/api/claims/{claim_id}/documents/{doc_id}/file")
def doc_file(claim_id: int, doc_id: int, user=Depends(require_user)):
    with db() as conn:
        claim = conn.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
        if not claim or not can_view_claim(user, claim):
            raise HTTPException(404, "票据不存在")
        row = conn.execute("SELECT * FROM documents WHERE id=? AND claim_id=?", (doc_id, claim_id)).fetchone()
    if not row:
        raise HTTPException(404, "票据不存在")
    path = config.UPLOAD_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(404, "原始文件缺失")
    return FileResponse(path, filename=row["original_name"], media_type="application/pdf",
                        headers={"Cache-Control": "private, max-age=600"})


# ---------- 任务 ----------

@app.get("/api/tasks/{task_id}")
def task_detail(task_id: int, user=Depends(require_user)):
    with db() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "任务不存在")
        claim = conn.execute("SELECT * FROM claims WHERE id=?", (row["claim_id"],)).fetchone()
        if claim and not can_view_claim(user, claim):
            raise HTTPException(403, "无权查看")
    data = dict(row)
    try:
        data["trace"] = json.loads(row["trace"] or "[]")
    except Exception:
        data["trace"] = []
    return data


@app.get("/api/tasks")
def list_tasks(limit: int = 12, user=Depends(require_user)):
    where, params = "1=1", []
    if user["role"] not in ("admin", "approver"):
        where = "c.applicant_id=?"
        params = [user["id"]]
    with db() as conn:
        rows = conn.execute(
            f"""SELECT t.*, c.claim_no, c.purpose FROM tasks t JOIN claims c ON c.id=t.claim_id
                WHERE {where} ORDER BY t.id DESC LIMIT ?""", [*params, limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["trace"] = json.loads(r["trace"] or "[]")
        except Exception:
            d["trace"] = []
        out.append(d)
    return out


# ---------- 政策 ----------

@app.get("/api/policies")
def list_policies(q: str = "", user=Depends(require_user)):
    if q.strip():
        return {"query": q, "items": search_policies(q, limit=5)}
    with db() as conn:
        rows = conn.execute("SELECT * FROM policies WHERE active=1 ORDER BY code").fetchall()
    return {"query": "", "items": [dict(r) for r in rows]}


# ---------- 审计 / 用户 ----------

@app.get("/api/audit")
def audit_logs(limit: int = 100, user=Depends(require_role("admin"))):
    with db() as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/users")
def users(user=Depends(require_role("admin"))):
    with db() as conn:
        rows = conn.execute("SELECT id, username, display_name, role, department, active FROM users").fetchall()
    return [dict(r) for r in rows]


class NoCacheStatic(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
        return response


app.mount("/static", NoCacheStatic(directory=STATIC), name="static")



