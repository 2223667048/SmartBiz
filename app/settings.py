"""Small configuration layer. Reads .env at startup, then environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def agent_mode() -> str:
    return os.getenv("AGENT_MODE", "mock").strip().lower()


def langflow_url() -> str:
    return os.getenv("LANGFLOW_URL", "http://127.0.0.1:7860").rstrip("/")


def db_path() -> Path:
    configured = Path(os.getenv("SMARTBIZ_DB_PATH", "./data/smartbiz.sqlite3"))
    return configured if configured.is_absolute() else PROJECT_ROOT / configured
