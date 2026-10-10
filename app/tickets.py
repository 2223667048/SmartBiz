"""Service module: human handoff tickets, independent from the Agent provider."""
import uuid
from .db import connection


def init_ticket_schema() -> None:
    with connection() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            subject TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new' CHECK(status IN ('new','in_progress','resolved')),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_ticket_session ON tickets(session_id);
        """)


def create_ticket(session_id: str, subject: str, description: str) -> str:
    ticket_id = str(uuid.uuid4())
    with connection() as conn:
        conn.execute("INSERT INTO tickets(id,session_id,subject,description) VALUES(?,?,?,?)",
                     (ticket_id, session_id, subject, description))
    return ticket_id


def list_my_tickets(session_id: str) -> list[dict]:
    with connection() as conn:
        result = conn.execute("""SELECT id,subject,description,status,created_at
                                 FROM tickets WHERE session_id=? ORDER BY created_at DESC, rowid DESC LIMIT 100""",
                              (session_id,)).fetchall()
    return [dict(row) for row in result]


def list_all_tickets(limit: int = 100) -> list[dict]:
    with connection() as conn:
        result = conn.execute("""SELECT id,session_id,subject,description,status,created_at
                                 FROM tickets ORDER BY created_at DESC, rowid DESC LIMIT ?""",
                              (limit,)).fetchall()
    return [dict(row) for row in result]


def set_ticket_status(ticket_id: str, status: str) -> bool:
    with connection() as conn:
        cursor = conn.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
        return cursor.rowcount > 0
