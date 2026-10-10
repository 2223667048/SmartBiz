"""Lesson 5: local-only administrator browser sessions.

This is NOT enterprise user identity: no SSO/MFA/roles/tenant separation.
Browser never stores the ADMIN_API_KEY. The cookie is HttpOnly, SameSite Strict.
"""
import hashlib
import hmac
import os
import secrets
import threading
import time

from . import db

COOKIE_NAME = "smartbiz_admin_session"
TTL_SECONDS = 8 * 3600
_attempts: dict[str, tuple[int, float]] = {}
_attempt_lock = threading.Lock()


def initialize():
    with db.connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_admin_sessions (
              id TEXT PRIMARY KEY,
              token_hash TEXT NOT NULL UNIQUE,
              key_version TEXT NOT NULL,
              expires_at INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_admin_expiry ON web_admin_sessions(expires_at)")


def configured() -> bool:
    return bool(os.getenv("ADMIN_API_KEY", ""))


def match_password(provided: str) -> bool:
    expected = os.getenv("ADMIN_API_KEY", "")
    return bool(expected and provided and hmac.compare_digest(provided, expected))


def throttle(source: str, valid: bool) -> bool:
    """Small in-process safeguard. Does not replace gateway rate limiting."""
    now = time.monotonic()
    with _attempt_lock:
        failures, reset = _attempts.get(source, (0, now + 300))
        if reset < now:
            failures, reset = 0, now + 300
        if failures >= 10:
            return False
        if valid:
            _attempts.pop(source, None)
        else:
            _attempts[source] = (failures + 1, reset)
        return True


def _key_version() -> str:
    """Invalidate existing browser sessions after administrator key rotation."""
    secret = os.getenv("ADMIN_API_KEY", "")
    return hmac.new(secret.encode(), b"smartbiz-admin-key-version", hashlib.sha256).hexdigest()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session() -> tuple[str, str, int]:
    token = secrets.token_urlsafe(40)
    identifier = secrets.token_hex(16)
    expires = int(time.time()) + TTL_SECONDS
    with db.connection() as conn:
        conn.execute("DELETE FROM web_admin_sessions WHERE expires_at < ?", (int(time.time()),))
        conn.execute("INSERT INTO web_admin_sessions(id,token_hash,key_version,expires_at) VALUES(?,?,?,?)",
                     (identifier, _digest(token), _key_version(), expires))
    return identifier, token, expires


def session_for_cookie(token: str | None) -> dict | None:
    if not token or len(token) > 256:
        return None
    with db.connection() as conn:
        row = conn.execute("SELECT id, expires_at FROM web_admin_sessions WHERE token_hash=? AND key_version=? AND expires_at>?",
                           (_digest(token), _key_version(), int(time.time()))).fetchone()
    return dict(row) if row else None


def csrf_for_cookie(token: str) -> str:
    secret = os.getenv("ADMIN_API_KEY", "")
    if not secret:
        raise RuntimeError("ADMIN_API_KEY missing")
    return hmac.new(secret.encode(), ("smartbiz-csrf:" + token).encode(), hashlib.sha256).hexdigest()


def valid_csrf(token: str, provided: str | None) -> bool:
    return bool(provided and hmac.compare_digest(provided, csrf_for_cookie(token)))


def logout(token: str | None) -> None:
    if not token:
        return
    with db.connection() as conn:
        conn.execute("DELETE FROM web_admin_sessions WHERE token_hash=?", (_digest(token),))


def secure_cookie() -> bool:
    return os.getenv("ADMIN_COOKIE_SECURE", "false").strip().lower() in ("1", "true", "yes")
