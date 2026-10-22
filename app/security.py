"""认证与权限：PBKDF2 口令、Cookie 会话、角色与数据范围。"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request

from . import config
from .db import db, now_iso

PBKDF2_ROUNDS = 120_000


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return f"pbkdf2${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ROUNDS)
    return hmac.compare_digest(candidate.hex(), digest)


def create_session(conn, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(config.CN_TZ) + timedelta(days=config.SESSION_DAYS)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO sessions(token, user_id, expires_at, created_at) VALUES (?,?,?,?)",
        (token, user_id, expires, now_iso()),
    )
    return token


def drop_session(conn, token: str) -> None:
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))


def get_user(request: Request):
    token = request.cookies.get("expense_session", "")
    if not token:
        return None
    with db() as conn:
        return conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id"
            " WHERE s.token = ? AND s.expires_at > ? AND u.active = 1",
            (token, now_iso()),
        ).fetchone()


def require_user(request: Request):
    user = get_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def require_role(*roles: str):
    def _dep(user=Depends(require_user)):
        if user["role"] not in roles:
            raise HTTPException(status_code=403, detail="当前账号无此操作权限")
        return user

    return _dep


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def can_view_claim(user, claim) -> bool:
    """员工只能看自己的单据，审批人与管理员看全部。"""
    if user["role"] in ("admin", "approver"):
        return True
    return claim["applicant_id"] == user["id"]
