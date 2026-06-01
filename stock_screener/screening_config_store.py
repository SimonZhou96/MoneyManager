import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

CONFIG_VERSION = 1
_CONFIG_FILENAME = "interactive_screening_last.json"


def config_path() -> str:
    runtime_home = os.environ.get("RUNTIME_HOME") or os.path.expanduser("~")
    return os.path.join(runtime_home, _CONFIG_FILENAME)


def load_last_config(path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    target_path = path or config_path()
    try:
        with open(target_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            return None
        if payload.get("version") != CONFIG_VERSION:
            return None
        answers = payload.get("answers")
        if not isinstance(answers, dict):
            return None
        return answers
    except (FileNotFoundError, ValueError, OSError):
        return None


def save_last_config(answers: Dict[str, Any], path: Optional[str] = None) -> bool:
    target_path = path or config_path()
    directory = os.path.dirname(target_path)
    payload = {
        "version": CONFIG_VERSION,
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "answers": answers,
    }
    try:
        os.makedirs(directory, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        return True
    except OSError:
        return False
