"""Install/remove the local, per-user macOS catalog worker."""
import argparse
import os
from pathlib import Path
import plistlib
import json
import shutil
import subprocess
import sys

from web.config import ROOT_DIR

LABEL = "com.moneymanager.stock-catalog"
SERVICE_ROOT = Path.home() / "Library/Application Support/MoneyManager/stock-catalog"


def prepare_runtime():
    """Keep a launchd runtime outside macOS's Desktop privacy boundary.

    Reinstall refreshes this snapshot after source or .env changes. No macOS
    privacy settings are modified, and the plist contains no credentials.
    """
    from dotenv import dotenv_values
    uv = shutil.which("uv")
    if not uv:
        raise RuntimeError("Installing the local worker requires uv on PATH")
    SERVICE_ROOT.mkdir(parents=True, exist_ok=True)
    SERVICE_ROOT.chmod(0o700)
    code = SERVICE_ROOT / "code"
    code.mkdir(exist_ok=True)
    for filename in ("db.py", "stock_pool.py", "strategy.py", "market.py"):
        shutil.copy2(ROOT_DIR / filename, code / filename)
    shutil.copytree(ROOT_DIR / "worker", code / "worker", dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    (code / "web").mkdir(exist_ok=True)
    for filename in ("__init__.py", "config.py"):
        shutil.copy2(ROOT_DIR / "web" / filename, code / "web" / filename)
    (code / "sql").mkdir(exist_ok=True)
    shutil.copy2(ROOT_DIR / "sql/021_stock_catalog_sync.sql", code / "sql/021_stock_catalog_sync.sql")
    freeze = subprocess.check_output([uv, "pip", "freeze", "--python", sys.executable], text=True)
    requirements = SERVICE_ROOT / "requirements.lock.txt"
    requirements.write_text(freeze)
    environment = SERVICE_ROOT / "venv"
    if not (environment / "bin/python").exists():
        subprocess.run([uv, "venv", str(environment), "--python", str(Path(sys._base_executable).resolve())], check=True)
    # Install exact already-installed versions from the local wheel cache.
    subprocess.run([uv, "pip", "install", "--offline", "--python", str(environment / "bin/python"),
                    "-r", str(requirements)], check=True)
    values = dotenv_values(ROOT_DIR / ".env", interpolate=False)
    values.update({key: value for key, value in os.environ.items()
                   if key.startswith(("MYSQL_", "STOCK_CATALOG_"))})
    values = {key: value for key, value in values.items()
              if key.startswith(("MYSQL_", "STOCK_CATALOG_")) and value is not None}
    values["STOCK_CATALOG_CACHE_DIR"] = str(SERVICE_ROOT / "cache")
    # Keep exact password characters without shell or dotenv interpolation.
    config = SERVICE_ROOT / "config.json"
    config.touch(mode=0o600, exist_ok=True)
    config.chmod(0o600)
    config.write_text(json.dumps(values, ensure_ascii=False, indent=2))
    (code / ".env").unlink(missing_ok=True)
    (SERVICE_ROOT / "source.json").write_text(json.dumps({"source_root": str(ROOT_DIR)}, indent=2))
    return code, environment / "bin/python"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "uninstall", "stop", "status"))
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("LaunchAgent is macOS-only; use python -m worker.stock_catalog --daemon remotely")
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{LABEL}"
    path = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    if args.action == "status":
        return subprocess.run(["launchctl", "print", target]).returncode
    subprocess.run(["launchctl", "bootout", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if args.action == "uninstall":
        path.unlink(missing_ok=True)
    elif args.action == "install":
        code, python = prepare_runtime()
        logs = SERVICE_ROOT / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        configuration = {
            "Label": LABEL,
            "ProgramArguments": [str(python), "-u", "-m", "worker.stock_catalog", "--daemon"],
            "WorkingDirectory": str(code), "RunAtLoad": True, "KeepAlive": True,
            "EnvironmentVariables": {"STOCK_CATALOG_CONFIG_FILE": str(SERVICE_ROOT / "config.json")},
            "ThrottleInterval": 60, "ProcessType": "Background",
            "StandardOutPath": str(logs / "worker.log"), "StandardErrorPath": str(logs / "worker-error.log"),
        }
        path.write_bytes(plistlib.dumps(configuration))
        path.chmod(0o600)
        subprocess.run(["launchctl", "bootstrap", domain, str(path)], check=True)
    print(f"{args.action}: {target}; plist: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
