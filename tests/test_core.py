"""报销预审数字员工：规则、抽取与接口回归测试。运行 pytest -q"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config
from app.services.invoice import detect_kind, parse_fields
from app.services.rules import _hotel_limit, run_rules, summarize


# ---------- 票据识别 ----------

def test_detect_kind():
    assert detect_kind("住宿服务 标准间", "a.pdf") == "hotel"
    assert detect_kind("中国铁路电子客票 G1652", "b.pdf") == "train"
    assert detect_kind("*餐饮服务*业务招待", "c.pdf") == "entertainment"
    assert detect_kind("*餐饮服务*餐费", "d.pdf") == "meal"


def test_parse_invoice_fields():
    text = (
        "厦门增值税电子普通发票\n"
        "发票代码：035002100311  发票号码：24567891\n"
        "开票日期：2026年10月09日\n"
        "购买方\n名称：厦门商集网络科技有限责任公司\n"
        "销售方\n名称：厦门海丝酒店管理有限公司\n"
        "*住宿服务*住宿费 标准间 2 400.00 800.00 6% 48.00\n"
        "价税合计（小写）：¥800.00\n"
        "入住日期：2026年10月08日\n离店日期：2026年10月10日\n共 2 晚\n"
    )
    f = parse_fields(text, "hotel")
    assert f["invoice_no"] == "24567891"
    assert f["invoice_date"] == "2026-10-09"
    assert f["buyer"] == "厦门商集网络科技有限责任公司"
    assert f["seller"] == "厦门海丝酒店管理有限公司"
    assert f["total"] == 800.0
    assert f["nights"] == 2
    assert f["city"] == "厦门"


def test_city_hotel_limit():
    assert _hotel_limit("上海") == 700
    assert _hotel_limit("厦门") == 450
    assert _hotel_limit("拉萨") == 380


# ---------- 规则 ----------

def _claim(**kw):
    base = {"id": 1, "claim_no": "BX0001", "category": "差旅费", "trip_start": "2026-10-08",
            "trip_end": "2026-10-10", "destination": "上海", "claimed_amount": 800.0,
            "submitted_at": "2026-10-12T09:00:00+08:00"}
    base.update(kw)
    return base


def _doc(total=800.0, buyer=None, invoice_no="24567891", date="2026-10-09", kind="hotel"):
    return {"id": 1, "kind": kind, "original_name": "a.pdf", "method": "text", "confidence": 1.0,
            "fields": {"kind": kind, "total": total, "buyer": buyer or config.COMPANY,
                       "invoice_no": invoice_no, "invoice_date": date, "city": "上海"}}


class FakeConn:
    def execute(self, *a, **kw):
        class R:
            def fetchone(self):
                return None
        return R()


def _run(claim, docs, items):
    return run_rules(claim, docs, items, {"conn": FakeConn()})


def _items(amounts):
    return [{"id": i + 1, "category": c, "amount": a, "occurred_on": "2026-10-09",
             "city": "上海", "merchant": "x", "invoice_no": f"I{i}", "nights": n}
            for i, (c, a, n) in enumerate(amounts)]


def test_clean_claim_passes():
    checks = _run(_claim(), [_doc()], _items([("住宿", 800.0, 2)]))
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    assert not fails and not warns
    assert any(c.status == "pass" for c in checks)
    decision = summarize(_claim(), checks, [_doc()], _items([("住宿", 800.0, 2)]))
    assert decision["decision"] == "pass"
    assert decision["approved_amount"] == 800.0


def test_hotel_over_limit_warns():
    checks = _run(_claim(), [_doc(total=1960.0)], _items([("住宿", 1960.0, 2)]))
    warn = [c for c in checks if c.code == "R003"]
    assert warn and warn[0].status == "warn"
    assert "700" in warn[0].detail


def test_wrong_buyer_blocks():
    checks = _run(_claim(), [_doc(buyer="别的公司")], _items([("住宿", 800.0, 1)]))
    fail = [c for c in checks if c.code == "R001" and c.status == "fail"]
    assert fail


def test_out_of_trip_date_blocks():
    checks = _run(_claim(), [_doc(date="2026-10-25")], _items([("住宿", 800.0, 1)]))
    codes = {c.code for c in checks if c.status == "fail"}
    assert "R007" in codes or "R008" in codes


def test_amount_mismatch_blocks():
    checks = _run(_claim(claimed_amount=900.0), [_doc()], _items([("住宿", 800.0, 2)]))
    assert [c for c in checks if c.code == "R011"]


def test_missing_trip_dates_blocks():
    checks = _run(_claim(trip_start="", trip_end=""), [_doc()], _items([("住宿", 800.0, 2)]))
    assert [c for c in checks if c.code == "R005"]


def test_high_risk_rejects_without_amount():
    checks = _run(_claim(), [_doc(buyer="别的公司")], _items([("住宿", 800.0, 1)]))
    decision = summarize(_claim(), checks, [_doc(buyer="别的公司")], _items([("住宿", 800.0, 1)]))
    assert decision["decision"] == "reject"
    assert decision["approved_amount"] == 0.0


def test_entertainment_threshold():
    claim = _claim(category="业务招待费", trip_start="2026-10-16", trip_end="2026-10-16")
    items = _items([("业务招待", 3200.0, 1)])
    checks = _run(claim, [_doc(total=3200.0, kind="entertainment")], items)
    assert [c for c in checks if c.code == "R014"]


# ---------- 接口 ----------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(config, "PAGE_DIR", tmp_path / "pages")
    monkeypatch.setattr(config, "DB_PATH", tmp_path / "expense.db")
    monkeypatch.setattr(config, "SEED_ON_START", False)
    for d in (config.UPLOAD_DIR, config.PAGE_DIR):
        d.mkdir(parents=True, exist_ok=True)

    from app import main
    from app.db import init_db

    init_db()
    from app.services.policies import seed_policies
    from app.services.seed import seed_users
    seed_policies()
    seed_users()
    with TestClient(main.app) as c:
        yield c


def login(client, u="approver", p="Approve@123"):
    r = client.post("/api/auth/login", json={"username": u, "password": p})
    assert r.status_code == 200, r.text
    return r


def test_login_and_role(client):
    login(client)
    me = client.get("/api/me").json()
    assert me["role"] == "approver"
    client.post("/api/auth/logout")
    assert client.get("/api/stats").status_code == 401


def test_employee_sees_own_only(client, tmp_path):
    """员工创建的报销单，其他员工看不到。"""
    from app.db import db
    from app.security import hash_password
    from app.db import now_iso
    with db() as conn:
        for u, n in (("e1", "员工一"), ("e2", "员工二")):
            conn.execute("INSERT INTO users(username,password_hash,display_name,role,department,created_at)"
                         " VALUES (?,?,?,?,?,?)", (u, hash_password("Demo@123"), n, "employee", "销售部", now_iso()))
        uid = conn.execute("SELECT id FROM users WHERE username='e1'").fetchone()["id"]
        conn.execute("INSERT INTO claims(claim_no,applicant_id,applicant_name,status,created_at,updated_at)"
                     " VALUES ('BX-T1',?,'员工一','pending_review',?,?)", (uid, now_iso(), now_iso()))
    login(client, "e1", "Demo@123")
    assert client.get("/api/claims").json()["total"] == 1
    client.post("/api/auth/logout")
    login(client, "e2", "Demo@123")
    assert client.get("/api/claims").json()["total"] == 0
    assert client.get("/api/claims/1").status_code == 403


def test_approve_flow(client):
    from app.db import db, now_iso
    with db() as conn:
        conn.execute("INSERT INTO users(username,password_hash,display_name,role,department,created_at)"
                     " VALUES ('ap','x','审批','approver','财务',?)", (now_iso(),))
        conn.execute("INSERT INTO claims(claim_no,applicant_name,status,claimed_amount,approved_amount,"
                     " ai_decision,created_at,updated_at) VALUES ('BX-A1','某人','pending_review',800,800,'review',?,?)",
                     (now_iso(), now_iso()))
    login(client)
    r = client.post("/api/claims/1/decision", json={"action": "approve", "approved_amount": 700, "comment": "按限额"})
    assert r.status_code == 200, r.text
    detail = client.get("/api/claims/1").json()
    assert detail["status"] == "approved"
    assert detail["approved_amount"] == 700
    assert detail["approvals"][0]["comment"] == "按限额"
    # 不能重复审批
    assert client.post("/api/claims/1/decision", json={"action": "approve"}).status_code == 400

