"""种子数据：演示账号、政策与 8 张报销单。"""
from __future__ import annotations

import json
import shutil
import uuid

from .. import config
from ..db import db, now_iso
from ..security import hash_password
from . import pipeline
from .policies import seed_policies

USERS = [
    ("admin", "Admin@123", "平台管理员", "admin", "财务共享中心"),
    ("approver", "Approve@123", "财务审批人", "approver", "财务共享中心"),
    ("chenwei", "Demo@123", "陈伟", "employee", "销售部"),
    ("zhangmin", "Demo@123", "张敏", "employee", "供应链部"),
    ("liuyang", "Demo@123", "刘洋", "employee", "渠道部"),
]


def seed_users() -> None:
    with db() as conn:
        if conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]:
            return
        for username, pwd, name, role, dept in USERS:
            conn.execute(
                "INSERT INTO users(username, password_hash, display_name, role, department, created_at)"
                " VALUES (?,?,?,?,?,?)",
                (username, hash_password(pwd), name, role, dept, now_iso()),
            )


def seed_demo_claims() -> None:
    manifest_path = config.SEED_DIR / "demo_claims.json"
    if not manifest_path.exists():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    with db() as conn:
        if conn.execute("SELECT COUNT(*) c FROM claims").fetchone()["c"]:
            return
        users = {r["username"]: dict(r) for r in conn.execute("SELECT * FROM users")}

    for item in manifest.get("claims", []):
        applicant = users.get(item["applicant"])
        if not applicant:
            continue
        with db() as conn:
            claim_no = pipeline.next_claim_no(conn)
            cur = conn.execute(
                "INSERT INTO claims(claim_no, applicant_id, applicant_name, department, purpose,"
                " trip_start, trip_end, destination, category, claimed_amount, status,"
                " created_at, submitted_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (claim_no, applicant["id"], applicant["display_name"], applicant["department"],
                 item["purpose"], item["trip_start"], item["trip_end"], item["destination"],
                 item["category"], item["claimed_amount"], "processing",
                 item["submitted_at"] or now_iso(), item["submitted_at"] or now_iso(), now_iso()),
            )
            claim_id = cur.lastrowid

        for doc_name in item["documents"]:
            src = config.SEED_DIR / "invoices" / doc_name
            if not src.exists():
                continue
            stored = config.UPLOAD_DIR / f"{uuid.uuid4().hex}_{doc_name}"
            shutil.copy2(src, stored)
            pipeline.parse_and_store(claim_id, stored, doc_name)

        with db() as conn:
            conn.execute("UPDATE claims SET status='submitted', updated_at=? WHERE id=?",
                         (now_iso(), claim_id))
        # 通过任务执行，保留完整 trace，便于演示执行轨迹
        task_id = pipeline.enqueue(claim_id, applicant["id"], use_thread=False)
        try:
            pipeline.execute_claim(claim_id, task_id)
        except Exception as exc:
            print(f"[seed] claim {claim_no} failed: {exc}")


def seed_all() -> None:
    seed_policies()
    seed_users()
    seed_demo_claims()

