from __future__ import annotations

import os
import sys
from pathlib import Path

from db import MySqlConfig


ROOT_DIR = Path(__file__).resolve().parents[1]


def _pyinstaller_dotenv() -> Path | None:
    """PyInstaller 打包后，.env 在 sys._MEIPASS 或可执行文件同级。"""
    if hasattr(sys, "_MEIPASS") and sys._MEIPASS:
        path = Path(sys._MEIPASS) / ".env"  # type: ignore[attr-defined]
        if path.exists():
            return path
    # 可执行文件同级的 .env
    exe_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else None
    if exe_dir:
        path = exe_dir / ".env"
        if path.exists():
            return path
    return None


def load_dotenv(path: Path | None = None) -> None:
    # 优先：显式路径
    if path and path.exists():
        env_path = path
    else:
        env_dotenv = os.environ.get("MONEYMANAGER_DOTENV", "").strip()
        env_path = (
            _pyinstaller_dotenv()
            or (Path(env_dotenv) if env_dotenv else None)
            or (ROOT_DIR / ".env")
        )
    if not env_path or not env_path.exists():
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
