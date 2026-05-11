from __future__ import annotations

import hashlib
import os
from datetime import date
from pathlib import Path
from typing import List

from db import MarketDatabase, MySqlConfig
from feishu_notifier import send_screening_result
from market import market_label, normalize_market
from scheduled_daily_job import get_default_screening_params, run_market_screening_worker


def run_web_screening_job(
    mysql_config: MySqlConfig,
    job_id: str,
    markets: List[str],
    timeframe: str,
    artifact_root: str,
    enable_ai_analysis: bool = True,
    send_feishu: bool = False,
    chain_key: str | None = None,
) -> None:
    db = MarketDatabase(mysql_config)
    db.init_web_schema()
    db.update_web_screening_job(job_id, "running")
    db.close()

    task_ids: List[str] = []
    try:
        root = Path(artifact_root)
        root.mkdir(parents=True, exist_ok=True)
        csv_base = str(root / "screening_result")
        today_str = date.today().strftime("%Y-%m-%d")
        params = get_default_screening_params()
        if chain_key:
            params["chain_key"] = chain_key
        webhook_url = os.getenv("FEISHU_WEBHOOK_URL", "").strip()

        for raw_market in markets:
            market = normalize_market(raw_market)
            result = run_market_screening_worker(
                mysql_config=mysql_config,
                market=market,
                timeframe=timeframe,
                default_params=params,
                csv_base=csv_base,
                today_str=today_str,
                verbose=False,
                enable_ai_analysis=enable_ai_analysis,
                chain_key=chain_key,
            )
            if result.task_id:
                task_ids.append(result.task_id)
            _record_artifacts(mysql_config, result.task_id or job_id, market, result.csv_paths)

            if send_feishu and webhook_url and result.csv_paths:
                summary = f"【Web筛选】{date.today()} - {market_label(market)}\n通过: {len(result.passed)} 只"
                send_screening_result(webhook_url, summary, result.csv_paths)

        db2 = MarketDatabase(mysql_config)
        db2.update_web_screening_job(job_id, "completed", task_ids=task_ids, finished=True)
        db2.close()
    except Exception as exc:
        db3 = MarketDatabase(mysql_config)
        db3.update_web_screening_job(
            job_id,
            "failed",
            task_ids=task_ids or None,
            error_message=f"{type(exc).__name__}: {exc}",
            finished=True,
        )
        db3.close()
        raise


def _record_artifacts(mysql_config: MySqlConfig, task_id: str, market: str, paths: List[str]) -> None:
    if not paths:
        return
    db = MarketDatabase(mysql_config)
    for path_text in paths:
        path = Path(path_text)
        if not path.exists():
            continue
        suffix = path.suffix.lower()
        artifact_type = "report" if suffix == ".md" else "csv"
        db.create_screening_artifact({
            "task_id": task_id,
            "market": market,
            "artifact_type": artifact_type,
            "file_name": path.name,
            "file_path": str(path.resolve()),
            "content_type": "text/markdown; charset=utf-8" if suffix == ".md" else "text/csv; charset=utf-8",
            "file_size": path.stat().st_size,
            "checksum": _sha256(path),
        })
    db.close()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
