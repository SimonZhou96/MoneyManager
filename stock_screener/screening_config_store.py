import json
import os
from datetime import datetime, timezone
from typing import Any

CONFIG_VERSION = 1
_CONFIG_FILENAME = "interactive_screening_last.json"


def _config_path() -> str:
    runtime_home = os.environ.get("RUNTIME_HOME") or os.path.expanduser("~")
    return os.path.join(runtime_home, _CONFIG_FILENAME)


def load_last_config() -> dict[str, Any] | None:
    path = _config_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != CONFIG_VERSION:
            return None
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            return None
        return answers
    except Exception:
        return None


def save_last_config(answers: dict[str, Any]) -> bool:
    path = _config_path()
    directory = os.path.dirname(path)
    payload = {
        "version": CONFIG_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "answers": answers,
    }
    try:
        os.makedirs(directory, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return True
    except Exception:
        return False
