"""Daily directory sync. Run from stock_screener: python -m worker.stock_catalog."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, date
import json
import logging
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time

from db import MarketDatabase
from web.config import load_dotenv, mysql_config_from_env, ROOT_DIR
from .providers import PHASES, Providers, Snapshot
from .repository import Repository, ZONE, lock_name
from .transport import Transport, BudgetExceeded

LOG = logging.getLogger("stock_catalog")


@dataclass(frozen=True)
class Settings:
    request_limit: int = 200
    max_seconds: int = 900
    interval: float = 1.0
    cache_dir: Path = ROOT_DIR / "logs/stock_catalog/cache"

    @classmethod
    def from_env(cls):
        settings = cls(int(os.getenv("STOCK_CATALOG_REQUEST_LIMIT", "200")),
                       int(os.getenv("STOCK_CATALOG_TIMEOUT_SEC", "900")),
                       float(os.getenv("STOCK_CATALOG_REQUEST_INTERVAL_SEC", "1")),
                       Path(os.getenv("STOCK_CATALOG_CACHE_DIR", str(cls.cache_dir))).resolve())
        if settings.request_limit < 1 or settings.max_seconds < 1 or settings.interval < 1:
            raise ValueError("Catalog limits must be positive; HTTP interval must be >=1 second")
        return settings


def today(now=None):
    return (now or datetime.now(ZONE)).astimezone(ZONE).date()


def due(status):
    return status not in {"success", "partial", "failed"}


def poll_delay(now=None):
    current = (now or datetime.now(ZONE)).astimezone(ZONE)
    midnight = datetime.combine(current.date() + timedelta(days=1), datetime.min.time(), tzinfo=ZONE)
    return max(0.1, min(60.0, (midnight - current).total_seconds()))


def log(event, **details):
    LOG.info(json.dumps({"at": datetime.now(ZONE).isoformat(), "event": event, **details}, ensure_ascii=False, default=str))


def load_configuration():
    load_dotenv()
    config_file = os.getenv("STOCK_CATALOG_CONFIG_FILE")
    if config_file:
        values = json.loads(Path(config_file).read_text())
        if not isinstance(values, dict):
            raise ValueError("Worker config must be a JSON object")
        for key, value in values.items():
            if not isinstance(key, str) or not key.startswith(("MYSQL_", "STOCK_CATALOG_")) or not isinstance(value, str):
                raise ValueError("Invalid worker config key/value")
            os.environ.setdefault(key, value)


def sync(db, settings, *, force=False, run_date=None, providers_factory=Providers):
    day = run_date or today()
    repo = Repository(db, day, settings.request_limit)
    started = time.monotonic()
    try:
        if not repo.start(force):
            log("skipped", run_date=day, reason="already completed or another worker holds the lock")
            return "skipped"
        for phase, _ in PHASES:
            repo.sources.setdefault(phase, {"status": "pending", "requests": 0})
        repo.save_sources()
        transport = Transport(repo.bump_attempt, deadline=started + settings.max_seconds, interval=settings.interval)
        providers = providers_factory(transport)
        cache = settings.cache_dir / day.isoformat()
        cache.mkdir(parents=True, exist_ok=True)
        for phase, market in PHASES:
            repo.phase = phase
            entry = repo.sources[phase]
            if entry.get("status") == "success" or (entry.get("status") == "partial" and not force):
                continue
            phase_started = time.monotonic()
            cached = cache / f"{phase}.json"
            if force and entry.get("status") == "partial":
                cached.unlink(missing_ok=True)
            try:
                if transport.remaining() <= 0:
                    raise BudgetExceeded("Time budget exhausted before source execution")
                if cached.exists():
                    item = json.loads(cached.read_text())
                    snapshot = Snapshot(**item)
                else:
                    snapshot = providers.fetch(phase)
                    if len(snapshot.records) > 100_000:
                        raise ValueError("Unexpected source size (>100000 rows)")
                    temporary = cached.with_suffix(".tmp")
                    temporary.write_text(json.dumps(snapshot.__dict__, ensure_ascii=False), encoding="utf-8")
                    temporary.replace(cached)
                metrics = repo.apply(market, phase, snapshot.records)
                entry.update(metrics, status="partial" if snapshot.warnings else "success",
                             warnings=snapshot.warnings, source_date=snapshot.source_date)
                entry.pop("error", None)
            except Exception as exc:
                if isinstance(exc, ValueError):
                    cached.unlink(missing_ok=True)
                entry.update(status="failed", error=f"{type(exc).__name__}: {exc}")
            entry["seconds"] = round(time.monotonic() - phase_started, 3)
            repo.save_sources()
            log("source_finished", source=phase, market=market, **entry)
        status = repo.finish()
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        rss_mb = rss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        log("finished", status=status, run_date=day, requests=repo.requests,
            seconds=round(time.monotonic() - started, 3), peak_rss_mb=round(rss_mb, 1))
        for directory in settings.cache_dir.iterdir():
            if directory.is_dir() and not directory.is_symlink():
                try:
                    expired = date.fromisoformat(directory.name) < day - timedelta(days=6)
                except ValueError:
                    continue
                if expired:
                    shutil.rmtree(directory)
        return status
    finally:
        repo.close()


def get_status(db, day=None):
    with db.conn.cursor() as cursor:
        cursor.execute("SHOW TABLES LIKE 'stock_catalog_sync_runs'")
        if not cursor.fetchone():
            return None
        cursor.execute("SELECT run_date,status,request_count,sources_json,started_at,finished_at,error_message FROM stock_catalog_sync_runs WHERE run_date=%s", (day or today(),))
        row = cursor.fetchone()
        if not row:
            return None
        result = dict(zip(("run_date", "status", "request_count", "sources", "started_at", "finished_at", "error"), row))
        if isinstance(result["sources"], str):
            result["sources"] = json.loads(result["sources"])
        return result


def run_child(settings, force=False):
    day = today()
    command = [sys.executable, "-u", "-m", "worker.stock_catalog", "--execute", "--run-date", day.isoformat()]
    if force:
        command.append("--force")
    child = subprocess.Popen(command, cwd=ROOT_DIR)
    try:
        return child.wait(timeout=settings.max_seconds)
    except subprocess.TimeoutExpired:
        child.terminate()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        db = MarketDatabase(mysql_config_from_env())
        repo = Repository(db, day, settings.request_limit)
        try:
            # Never finalize someone else's still-running worker.
            with db.conn.cursor() as cursor:
                cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name(db),))
                repo.locked = cursor.fetchone()[0] == 1
            state = get_status(db, day)
            if repo.locked and state and state["status"] == "running":
                repo.sources = state["sources"]
                repo.finish("Worker exceeded configured wall-clock timeout")
        finally:
            repo.close()
            db.close()
        log("timeout", max_seconds=settings.max_seconds)
        return 124


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--daemon", action="store_true")
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--execute", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--force", action="store_true", help="Retry today's failed sources; daily HTTP budget is retained")
    parser.add_argument("--run-date", type=date.fromisoformat, help=argparse.SUPPRESS)
    args = parser.parse_args()
    load_configuration()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    settings = Settings.from_env()
    if args.execute or args.status:
        db = MarketDatabase(mysql_config_from_env())
        try:
            if args.status:
                print(json.dumps(get_status(db), ensure_ascii=False, default=str, indent=2))
                return 0
            result = sync(db, settings, force=args.force, run_date=args.run_date)
            return 0 if result in {"success", "skipped"} else 1
        finally:
            db.close()
    if not args.daemon:
        return run_child(settings, args.force)
    log("daemon_started", timezone="Asia/Shanghai", schedule="00:00 daily", catch_up=True)
    while True:
        try:
            db = MarketDatabase(mysql_config_from_env())
            try:
                state = get_status(db)
            finally:
                db.close()
            if due(state["status"] if state else None):
                run_child(settings)
        except Exception:
            LOG.exception("Catalog daemon check failed; retrying in 60 seconds")
        time.sleep(poll_delay())


if __name__ == "__main__":
    raise SystemExit(main())
