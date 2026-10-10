"""SQLite persistence. All SQL uses bound parameters."""
import hashlib
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from .settings import db_path


@contextmanager
def connection():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize():
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            role TEXT NOT NULL CHECK (role IN ('user','assistant')),
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            name TEXT NOT NULL,
            contact TEXT NOT NULL,
            requirement TEXT NOT NULL,
            consent INTEGER NOT NULL CHECK(consent IN (0,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS feedback (
            message_id TEXT PRIMARY KEY REFERENCES messages(id),
            session_id TEXT NOT NULL REFERENCES sessions(id),
            score INTEGER NOT NULL CHECK (score IN (-1,1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE INDEX IF NOT EXISTS idx_leads_created ON leads(created_at);
        """)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_session() -> tuple[str, str]:
    sid = str(uuid.uuid4())
    token = secrets.token_urlsafe(32)
    with connection() as conn:
        conn.execute("INSERT INTO sessions (id, token_hash) VALUES (?, ?)", (sid, _hash(token)))
    return sid, token


def session_for_token(token: str) -> str | None:
    if not token or len(token) > 256:
        return None
    with connection() as conn:
        row = conn.execute("SELECT id FROM sessions WHERE token_hash=?", (_hash(token),)).fetchone()
    return row["id"] if row else None


def insert_message(session_id: str, role: str, content: str) -> str:
    mid = str(uuid.uuid4())
    with connection() as conn:
        conn.execute("INSERT INTO messages (id,session_id,role,content) VALUES(?,?,?,?)",
                     (mid, session_id, role, content))
    return mid


def message_count(session_id: str) -> int:
    with connection() as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM messages WHERE session_id=?", (session_id,)).fetchone()
    return row["n"]


def add_lead(session_id: str, name: str, contact: str, requirement: str):
    lead_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute("INSERT INTO leads(id,session_id,name,contact,requirement,consent) VALUES (?,?,?,?,?,1)",
                     (lead_id, session_id, name, contact, requirement))
    return lead_id


def save_feedback(session_id: str, message_id: str, score: int) -> bool:
    with connection() as conn:
        row = conn.execute("SELECT 1 FROM messages WHERE id=? AND session_id=? AND role='assistant'",
                           (message_id, session_id)).fetchone()
        if not row:
            return False
        conn.execute("""INSERT INTO feedback(message_id,session_id,score) VALUES(?,?,?)
                     ON CONFLICT(message_id) DO UPDATE SET score=excluded.score""",
                     (message_id, session_id, score))
    return True


def admin_stats() -> dict:
    with connection() as conn:
        return {
            key: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for key, table in (("sessions", "sessions"), ("messages", "messages"),
                               ("leads", "leads"), ("feedback", "feedback"))
        }


def recent_leads(limit=100) -> list[dict]:
    with connection() as conn:
        rows = conn.execute("""SELECT id,name,contact,requirement,created_at
                               FROM leads ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                            (limit,)).fetchall()
    return [dict(row) for row in rows]


def recent_messages(limit=100) -> list[dict]:
    with connection() as conn:
        rows = conn.execute("""SELECT session_id,role,content,created_at
                               FROM messages ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                            (limit,)).fetchall()
    return [dict(row) for row in rows]


def list_session_messages(session_id: str, limit: int = 50) -> list[dict]:
    """Return only this session's latest messages in chronological order."""
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT id, role, content, created_at
            FROM (
                SELECT rowid AS sort_order, id, role, content, created_at
                FROM messages
                WHERE session_id = ?
                ORDER BY rowid DESC
                LIMIT ?
            )
            ORDER BY sort_order ASC
            """,
            (session_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]
