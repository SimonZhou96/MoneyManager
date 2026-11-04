"""Task-owned report generation, checkpointing, and MySQL persistence."""
from __future__ import annotations

import os
import time
from datetime import date, datetime, timezone
from urllib.parse import quote

from feishu_notifier import send_feishu_text
from report_store import MySqlReportRepository
from signal_analysis.models import ScreeningSignalRow, SignalAnalysisResult
from signal_analysis.report_content import ReportContentBuilder
from signal_analysis.service import run_signal_analysis_for_market


class TaskReportPublisher:
    """Resume AI analysis and publish one structured report per screening task."""

    def __init__(self, mysql_config, database):
        self.mysql_config = mysql_config
        self.database = database
        self.repository = MySqlReportRepository(database)
        self.content_builder = ReportContentBuilder()

    def publish(self, task_type: str, task_id: str, markets: list[str], timeframe: str,
                records_by_market: dict, chain_key: str = "", lock_token: str | None = None):
        checkpoint = self.database.get_task_report_checkpoint(task_type, task_id) or {}
        report_id = f"{task_type}-{task_id}"

        def save_checkpoint():
            if not self.database.save_task_report_checkpoint(
                task_type, task_id, checkpoint, lock_token=lock_token,
            ):
                raise RuntimeError("任务已终止或执行令牌已变化，停止生成报告")

        checkpoint.setdefault("records_by_market", records_by_market)
        checkpoint.setdefault("task_date", date.today().isoformat())
        checkpoint.setdefault("markets", markets)
        checkpoint.setdefault("timeframe", timeframe)
        checkpoint.setdefault("chain_key", chain_key)
        if checkpoint.get("status") == "stored":
            save_checkpoint()
            existing = self.repository.get(report_id)
            if existing:
                return existing
            raise RuntimeError("任务已完成，但 MySQL 报告记录缺失")

        records_by_market = checkpoint["records_by_market"]
        passed_count = sum(len(rows) for rows in records_by_market.values())
        if not passed_count:
            checkpoint = {"status": "not_required", "report_id": None, "reason": "没有入选股票"}
            save_checkpoint()
            return None

        checkpoint["report_id"] = report_id
        checkpoint.setdefault("results_by_market", {})
        checkpoint.setdefault("content_by_market", {})
        # Older in-flight jobs checkpointed Markdown. Rebuild the structured draft from
        # their saved analysis results instead of persisting the legacy representation.
        if checkpoint.get("draft") and "content" not in checkpoint["draft"]:
            checkpoint.pop("draft", None)
        checkpoint.pop("markdown_by_market", None)
        save_checkpoint()

        if checkpoint.get("status") in {"saving", "stored"}:
            existing = self.repository.get(report_id)
            if existing:
                checkpoint = {"status": "stored", "report_id": report_id}
                save_checkpoint()
                return existing

        if "draft" not in checkpoint:
            checkpoint["status"] = "analyzing"
            save_checkpoint()
            for market, records in records_by_market.items():
                if not records or market in checkpoint["content_by_market"]:
                    continue
                rows = [ScreeningSignalRow.from_record(row, index, market)
                        for index, row in enumerate(records)]

                def record_results(results):
                    checkpoint["results_by_market"][market] = {
                        code: {**result.to_db_row(
                            task_id, market, date.fromisoformat(checkpoint["task_date"]),
                        ), "check_date": checkpoint["task_date"]}
                        for code, result in results.items()
                        if result.analysis_status == "success"
                    }
                    save_checkpoint()

                initial = {
                    code: SignalAnalysisResult.from_llm_item(item, model=item.get("model") or "checkpoint")
                    for code, item in checkpoint["results_by_market"].get(market, {}).items()
                }
                result = run_signal_analysis_for_market(
                    mysql_config=self.mysql_config,
                    task_id=f"{task_id}:{market}",
                    market=market,
                    rows=rows,
                    check_date=date.fromisoformat(checkpoint["task_date"]),
                    timeframe=checkpoint["timeframe"],
                    chain_key=checkpoint["chain_key"],
                    enabled=True,
                    initial_results=initial,
                    on_results=record_results,
                )
                record_results(result.results_by_code)
                if not result.success or not result.report_content:
                    raise RuntimeError(result.skipped_reason or "AI 报告未完整生成")
                checkpoint["content_by_market"][market] = result.report_content
                save_checkpoint()

            names = [row.get("name") or row.get("code") or "" for rows in records_by_market.values() for row in rows]
            codes = sorted({alias for rows in records_by_market.values() for row in rows
                            for alias in ((row.get("code") or ""), (row.get("code") or "").split(".")[-1])
                            if alias})
            content = self.content_builder.build_task_content(
                task_id=task_id,
                task_type=task_type,
                task_date=checkpoint["task_date"],
                markets=list(checkpoint["markets"]),
                timeframe=checkpoint["timeframe"],
                chain_key=checkpoint["chain_key"],
                market_content=checkpoint["content_by_market"],
                passed_count=passed_count,
            )
            checkpoint["draft"] = {
                "report_id": report_id,
                "task_id": task_id,
                "task_type": task_type,
                "title": f"筛选报告 · {task_id}",
                "summary": f"{checkpoint['task_date']}，入选 {passed_count} 只：" + "、".join(names[:8]),
                "markets": list(checkpoint["markets"]),
                "timeframe": checkpoint["timeframe"],
                "chain_key": checkpoint["chain_key"],
                "codes": codes,
                "names": names,
                "passed_count": passed_count,
                "task_date": checkpoint["task_date"],
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "content": content,
            }

        checkpoint["status"] = "saving"
        checkpoint["draft"]["revision"] = time.time_ns()
        save_checkpoint()
        self.repository.save(checkpoint["draft"])
        report = checkpoint["draft"]
        checkpoint = {"status": "stored", "report_id": report_id}
        save_checkpoint()
        return report


def publish_task_report(mysql_config, db, task_type: str, task_id: str, markets: list[str], timeframe: str,
                        records_by_market: dict, chain_key: str = "", lock_token: str | None = None):
    """Compatibility entry point used by screening jobs and retry workers."""
    return TaskReportPublisher(mysql_config, db).publish(
        task_type, task_id, markets, timeframe, records_by_market, chain_key, lock_token,
    )


def notify_task_report(report: dict | None, task_id: str):
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        return
    text = f"选股任务 {task_id} 已完成"
    if report:
        text += f"\n入选 {report['passed_count']} 只\n报告 ID：{report['report_id']}"
        url = os.getenv("WEB_PUBLIC_URL", "").strip().rstrip("/")
        if url:
            text += f"\n{url}/?page=reports&report_id={quote(report['report_id'], safe='')}"
    else:
        text += "\n没有入选股票，未生成 AI 报告"
    return send_feishu_text(webhook, text)
