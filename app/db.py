"""SQLite 数据层：账户、报销单、票据、明细、规则校验、任务、审批与审计。"""
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('admin','approver','employee')),
    department TEXT DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    category TEXT DEFAULT '',
    content TEXT NOT NULL,
    effective_date TEXT DEFAULT '',
    version TEXT DEFAULT 'v1',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_no TEXT UNIQUE NOT NULL,
    applicant_id INTEGER REFERENCES users(id),
    applicant_name TEXT DEFAULT '',
    department TEXT DEFAULT '',
    purpose TEXT DEFAULT '',
    trip_start TEXT DEFAULT '',
    trip_end TEXT DEFAULT '',
    destination TEXT DEFAULT '',
    category TEXT DEFAULT '差旅费',
    claimed_amount REAL NOT NULL DEFAULT 0,
    approved_amount REAL DEFAULT 0,
    currency TEXT DEFAULT 'CNY',
    status TEXT NOT NULL DEFAULT 'submitted',
    ai_decision TEXT DEFAULT '',
    ai_confidence REAL DEFAULT 0,
    ai_summary TEXT DEFAULT '',
    ai_source TEXT DEFAULT '',
    risk_level TEXT DEFAULT '',
    assignee_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    submitted_at TEXT DEFAULT '',
    decided_at TEXT DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    kind TEXT NOT NULL DEFAULT 'invoice',
    original_name TEXT DEFAULT '',
    stored_name TEXT DEFAULT '',
    image_path TEXT DEFAULT '',
    text TEXT DEFAULT '',
    method TEXT DEFAULT 'text',
    confidence REAL DEFAULT 1,
    fields TEXT DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS line_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    doc_id INTEGER REFERENCES documents(id) ON DELETE SET NULL,
    category TEXT NOT NULL,
    occurred_on TEXT DEFAULT '',
    city TEXT DEFAULT '',
    merchant TEXT DEFAULT '',
    invoice_no TEXT DEFAULT '',
    amount REAL NOT NULL DEFAULT 0,
    tax_amount REAL DEFAULT 0,
    description TEXT DEFAULT '',
    confidence REAL DEFAULT 1,
    source TEXT DEFAULT 'ai',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    policy_id INTEGER REFERENCES policies(id) ON DELETE SET NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    status TEXT NOT NULL DEFAULT 'pass',
    title TEXT NOT NULL,
    detail TEXT DEFAULT '',
    evidence TEXT DEFAULT '',
    suggestion TEXT DEFAULT '',
    source TEXT DEFAULT 'rule',
    confidence REAL DEFAULT 1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER REFERENCES claims(id) ON DELETE CASCADE,
    stage TEXT DEFAULT '排队中',
    status TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    trace TEXT DEFAULT '[]',
    error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS approvals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    approver_id INTEGER REFERENCES users(id),
    approver_name TEXT DEFAULT '',
    action TEXT NOT NULL,
    comment TEXT DEFAULT '',
    ai_decision TEXT DEFAULT '',
    approved_amount REAL DEFAULT 0,
    corrections TEXT DEFAULT '[]',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    claim_id INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
    check_id INTEGER REFERENCES checks(id) ON DELETE SET NULL,
    rule_code TEXT DEFAULT '',
    ai_label TEXT DEFAULT '',
    human_label TEXT DEFAULT '',
    note TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    username TEXT DEFAULT '',
    action TEXT NOT NULL,
    target_type TEXT DEFAULT '',
    target_id TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    ip TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_claims_status ON claims(status);
CREATE INDEX IF NOT EXISTS idx_claims_applicant ON claims(applicant_id);
CREATE INDEX IF NOT EXISTS idx_docs_claim ON documents(claim_id);
CREATE INDEX IF NOT EXISTS idx_items_claim ON line_items(claim_id);
CREATE INDEX IF NOT EXISTS idx_checks_claim ON checks(claim_id);
CREATE INDEX IF NOT EXISTS idx_tasks_claim ON tasks(claim_id);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);

CREATE VIRTUAL TABLE IF NOT EXISTS policies_fts USING fts5(
    title, content, content='policies', content_rowid='id', tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS policies_ai AFTER INSERT ON policies BEGIN
    INSERT INTO policies_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;

CREATE TRIGGER IF NOT EXISTS policies_ad AFTER DELETE ON policies BEGIN
    INSERT INTO policies_fts(policies_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
END;

CREATE TRIGGER IF NOT EXISTS policies_au AFTER UPDATE ON policies BEGIN
    INSERT INTO policies_fts(policies_fts, rowid, title, content) VALUES ('delete', old.id, old.title, old.content);
    INSERT INTO policies_fts(rowid, title, content) VALUES (new.id, new.title, new.content);
END;
"""


def now_iso() -> str:
    return datetime.now(config.CN_TZ).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(config.CN_TZ).strftime("%Y-%m-%d")


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with db() as conn:
        conn.executescript(SCHEMA)
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(claims)")}
        if "ai_source" not in cols:
            conn.execute("ALTER TABLE claims ADD COLUMN ai_source TEXT DEFAULT ''")


def audit(conn, user, action: str, target_type: str = "", target_id="", detail: str = "", ip: str = "") -> None:
    conn.execute(
        "INSERT INTO audit_logs(user_id, username, action, target_type, target_id, detail, ip, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (user["id"] if user else None, user["username"] if user else "", action,
         target_type, str(target_id), detail, ip, now_iso()),
    )
