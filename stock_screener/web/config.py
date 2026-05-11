from __future__ import annotations

import os
from pathlib import Path

from db import MySqlConfig


ROOT_DIR = Path(__file__).resolve().parents[1]


def load_dotenv(path: Path | None = None) -> None:
    env_path = path or ROOT_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def mysql_config_from_env() -> MySqlConfig:
    return MySqlConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", "123456"),
        database=os.getenv("MYSQL_DATABASE", "market_data"),
    )


def artifact_dir() -> Path:
    path = Path(os.getenv("ARTIFACT_DIR", str(ROOT_DIR / "logs" / "web_artifacts")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def session_cookie_name() -> str:
    return os.getenv("WEB_SESSION_COOKIE", "moneymanager_session")


def session_ttl_hours() -> int:
    return int(os.getenv("WEB_SESSION_TTL_HOURS", "24"))


def cookie_secure() -> bool:
    return os.getenv("WEB_COOKIE_SECURE", "1").strip().lower() in {"1", "true", "yes", "on"}


def agent_token() -> str:
    return os.getenv("AGENT_TOKEN", "").strip()
