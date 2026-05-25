#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MySQL 存储层

设计要点：
- stocks：股票主数据，(market, code) 唯一约束
- ema_breakout_signals_{timeframe}：按 timeframe 分表，每张表只存一种周期的信号
- screening_results：筛选结果
- stock_kline_cache：云端 Web 模式下的 K 线缓存，优先由本地 OpenD Agent 推送
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
from stock_pool import DEFAULT_POOL_TYPES_TEXT
from strategy import TECHNICAL_PATTERN_DEFINITIONS

try:
    import pymysql
except Exception as e:  # pragma: no cover
    pymysql = None
    _PYMYSQL_IMPORT_ERROR = e


@dataclass(frozen=True)
class MySqlConfig:
    host: str
    port: int
    user: str
    password: str
    database: str
    charset: str = "utf8mb4"
    connect_timeout: int = 10


def _safe_table_suffix(timeframe: str) -> str:
    """将 timeframe 转为安全的表名后缀（仅字母数字）"""
    return re.sub(r"[^a-z0-9]", "", timeframe.lower())


def _decode_json_field(value: Any, default: Any):
    """兼容 PyMySQL 返回 JSON 字符串或已解析对象两种情况。"""
    if value is None or value == "":
        return default
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return default
    return value


def _json_or_none(value: Any):
    """Encode structured values for MySQL JSON columns."""
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _option_payload(item: Any) -> dict:
    """Normalize option_lab dataclasses or dicts to repository payloads."""
    if isinstance(item, dict):
        payload = dict(item)
    elif hasattr(item, "to_dict"):
        payload = dict(item.to_dict())
    else:
        payload = {}
    for key in (
        "candidate_id", "run_id", "market", "code", "strategy_key", "strategy_name",
        "score", "recommendation_status", "fit_reason", "snapshot_id", "plan_id",
        "position_id", "status", "event_id", "event_type", "message", "severity", "provider",
    ):
        if key not in payload and hasattr(item, key):
            payload[key] = getattr(item, key)
    for key in ("contract_details", "warnings"):
        if key not in payload and hasattr(item, key):
            values = getattr(item, key) or []
            payload[key] = [value.to_dict() if hasattr(value, "to_dict") else value for value in values]
    if "data_quality" not in payload and hasattr(item, "data_quality"):
        value = getattr(item, "data_quality")
        payload["data_quality"] = value.to_dict() if hasattr(value, "to_dict") else value
    return payload


OPTION_CANDIDATE_MACRO_FIELD_KEYS = (
    "期权评分",
    "宏观分析评分",
    "综合评分",
    "宏观方向",
    "新闻影响",
    "热点匹配",
    "主力资金风险",
    "宏观摘要",
    "关键利好因素",
    "关键风险因素",
    "宏观/政策因素",
    "信息来源",
    "数据缺失原因",
    "引用来源",
    "因素引用",
)


def _option_candidate_macro_fields(item: dict) -> dict:
    return {
        key: item.get(key)
        for key in OPTION_CANDIDATE_MACRO_FIELD_KEYS
        if item.get(key) not in (None, "", [])
    }


class InMemoryQuantRepository:
    def __init__(self):
        self.runs = {}

    def create_quant_backtest_run(self, row: dict) -> None:
        self.runs[row["run_id"]] = {
            **row,
            "metrics": {},
            "chart": {},
            "warnings": list(row.get("warnings") or []),
            "progress_pct": int(row.get("progress_pct") or 0),
            "current_stage": row.get("current_stage") or "",
            "progress_logs": list(row.get("progress_logs") or []),
            "error_message": row.get("error_message"),
        }

    def update_quant_backtest_progress(
        self,
        run_id: str,
        *,
        status: str | None = None,
        progress_pct: int | None = None,
        current_stage: str | None = None,
        log_message: str | None = None,
    ) -> None:
        row = self.runs[run_id]
        if status is not None:
            row["status"] = status
        if progress_pct is not None:
            row["progress_pct"] = max(0, min(100, int(progress_pct)))
        if current_stage is not None:
            row["current_stage"] = current_stage
        if log_message:
            row.setdefault("progress_logs", []).append(_progress_log_entry(log_message))

    def finish_quant_backtest_run(self, run_id: str, metrics: dict, warnings: list[str], chart: dict | None = None) -> None:
        row = self.runs[run_id]
        row["status"] = "completed"
        row["progress_pct"] = 100
        row["current_stage"] = "回测完成"
        row["metrics"] = metrics
        row["chart"] = chart or {}
        row["warnings"] = warnings
        row.setdefault("progress_logs", []).append(_progress_log_entry("回测完成"))

    def fail_quant_backtest_run(self, run_id: str, error_message: str, warnings: list[str] | None = None) -> None:
        row = self.runs[run_id]
        row["status"] = "failed"
        row["progress_pct"] = max(int(row.get("progress_pct") or 0), 100)
        row["current_stage"] = "执行失败"
        row["error_message"] = error_message
        if warnings is not None:
            row["warnings"] = warnings
        row.setdefault("progress_logs", []).append(_progress_log_entry(error_message))

    def get_quant_backtest_run(self, run_id: str) -> dict | None:
        return self.runs.get(run_id)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _progress_log_entry(message: str) -> dict:
    return {"time": _utcnow().isoformat(timespec="seconds"), "message": str(message)}


def _mysql_datetime_or_none(value: Any):
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return value


def hash_password(password: str, *, iterations: int = 260_000) -> str:
    """Return a PBKDF2-SHA256 password hash string."""
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verify PBKDF2-SHA256 password hashes created by hash_password."""
    try:
        algorithm, iterations_raw, salt, digest_hex = str(password_hash).split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
        return secrets.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def session_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


DEFAULT_RULE_MARKETS = ("HK", "US", "A")
DEFAULT_RULE_CHAIN_KEY = "default_zuoyi_and_other"


def _technical_pattern_rule_rows() -> tuple:
    """根据通用技术形态定义生成默认原子规则元数据。"""
    rows = []
    display_order = 300
    for pattern_key, definition in TECHNICAL_PATTERN_DEFINITIONS.items():
        label = str(definition.get("label") or pattern_key)
        direction = str(definition.get("direction") or "neutral")
        group = {"bullish": "看涨规则", "bearish": "看跌规则", "neutral": "中性规则"}.get(direction, "其他规则")
        rows.append((
            pattern_key,
            label,
            "strategy",
            "technical",
            "TechnicalPatternStrategizer",
            {
                "pattern_key": pattern_key,
                "pattern_label": label,
                "direction": direction,
                "display_group": group,
            },
            True,
            display_order,
            f"{group}：{label}",
        ))
        display_order += 10
    return tuple(rows)


DEFAULT_RULE_METADATA = (
    ("market_cap_range", "市值范围", "filter", "", "MarketCapFilter",
     {"min_cap": None, "max_cap": None}, True, 10, "按市值上下限筛选"),
    ("avg_daily_volume_range", "每日平均交易量范围", "filter", "", "AvgDailyVolumeFilter",
     {"min_volume": None, "max_volume": None}, True, 20, "按 K 线计算每日平均交易量"),
    ("price_range", "价格范围", "filter", "", "PriceFilter",
     {"min_price": None, "max_price": None}, True, 30, "按最新收盘价筛选"),
    ("pe_range", "PE 范围", "filter", "", "PEFilter",
     {"min_pe": None, "max_pe": None, "allow_negative": False}, True, 40, "按 PE 上下限筛选"),
    ("profitability", "公司盈利", "filter", "", "ProfitabilityFilter",
     {"require_profitable": True}, True, 50, "要求 PE 为正"),
    ("zuoyi_signal", "左一战法", "strategy", "technical", "ZuoYiStrategizer",
     {"signal_window": 15, "include_bullish": True, "include_bearish": True}, True, 110,
     "当前周期15根K线内左一战法看涨/看跌信号"),
    ("ema_breakout", "EMA 突破", "strategy", "technical", "EMABreakoutStrategizer",
     {"ema_short": 10, "ema_long": 150}, True, 120, "EMA 短线向上突破长线"),
    ("rsi_oversold", "RSI 超卖", "strategy", "technical", "RSIOversoldStrategizer",
     {"period": 14, "threshold": 30.0}, True, 130, "RSI 低于等于阈值"),
    ("rsi_overbought", "RSI 超买", "strategy", "technical", "RSIOverboughtStrategizer",
     {"period": 14, "threshold": 70.0}, True, 140, "RSI 高于等于阈值"),
    ("volume_spike_prior3", "放量超前三日", "strategy", "technical", "TodayVolumeExceedsPrior3MaxStrategizer",
     {}, True, 150, "当日成交量大于前三日最大值"),
    ("daily_drop_6_65", "当日跌 6%~6.5%", "strategy", "technical", "DailyDrop6To65Strategizer",
     {"pct_min": -6.5, "pct_max": -6.0}, True, 160, "当日跌幅在指定区间"),
    ("daily_rise_4_45", "当日涨 4%~4.5%", "strategy", "technical", "DailyRise4To45Strategizer",
     {"pct_min": 4.0, "pct_max": 4.5}, True, 170, "当日涨幅在指定区间"),
    ("company_event_hot_sector_link", "公司时事与热点板块关联", "strategy", "macro", "CompanyEventHotSectorStrategizer",
     {}, True, 210, "复用 AI 分析结果，判断公司时事是否与热点板块形成共振"),
    ("company_event_hot_news_link", "公司时事与热点新闻关联", "strategy", "macro", "CompanyEventHotNewsStrategizer",
     {}, True, 220, "复用 AI 分析结果，判断公司时事是否被热点新闻验证"),
) + _technical_pattern_rule_rows()


US_DEFAULT_RULE_PARAM_OVERRIDES = {
    "market_cap_range": {"min_cap": 5_000_000_000, "max_cap": None, "min_exclusive": True},
    "avg_daily_volume_range": {
        "min_volume": 20_000_000,
        "max_volume": None,
        "lookback_days": 10,
        "metric": "turnover",
        "min_exclusive": True,
    },
    "price_range": {"min_price": 5, "max_price": None, "min_exclusive": True},
    "pe_range": {"min_pe": 5, "max_pe": None, "allow_negative": False, "min_exclusive": True},
}


US_DEFAULT_RULE_FIELD_OVERRIDES = {
    "avg_daily_volume_range": {
        "rule_name": "10天平均成交额范围",
        "description": "复用每日平均交易量规则，按 K 线计算最近10天平均成交额",
    },
}


DEFAULT_RULE_CHAIN_EXPRESSION = {
    "and": [
        {
            "all_enabled": [
                "market_cap_range",
                "avg_daily_volume_range",
                "price_range",
                "pe_range",
                "profitability",
            ]
        },
        {"ref": "zuoyi_signal"},
        {
            "any_enabled": [
                "ema_breakout",
                "rsi_oversold",
                "rsi_overbought",
                "volume_spike_prior3",
                "daily_drop_6_65",
                "daily_rise_4_45",
            ]
        },
    ]
}


TREND_CAPITAL_ACCUMULATION_WATCH_EXPRESSION = {
    "and": [
        {
            "all_enabled": [
                "market_cap_range",
                "avg_daily_volume_range",
                "price_range",
                "pe_range",
                "profitability",
            ]
        },
        {"ref": "zuoyi_signal"},
        {
            "any_enabled": [
                "ema_breakout",
                "volume_spike_prior3",
                "daily_rise_4_45",
            ]
        },
    ]
}


def _default_rule_params_for_market(market: str, rule_key: str, params: dict) -> dict:
    result = dict(params or {})
    if market == "US":
        result.update(US_DEFAULT_RULE_PARAM_OVERRIDES.get(rule_key, {}))
    return result


def _default_rule_name_for_market(market: str, rule_key: str, rule_name: str) -> str:
    if market == "US":
        return US_DEFAULT_RULE_FIELD_OVERRIDES.get(rule_key, {}).get("rule_name", rule_name)
    return rule_name


def _default_rule_description_for_market(market: str, rule_key: str, description: str) -> str:
    if market == "US":
        return US_DEFAULT_RULE_FIELD_OVERRIDES.get(rule_key, {}).get("description", description)
    return description


def _default_rule_chain_expression_for_market(market: str) -> dict:
    return DEFAULT_RULE_CHAIN_EXPRESSION


class MarketDatabase:
    """MySQL 存储层"""

    def __init__(self, config: MySqlConfig):
        if pymysql is None:  # pragma: no cover
            raise RuntimeError("缺少 MySQL 驱动 PyMySQL") from _PYMYSQL_IMPORT_ERROR

        self.config = config
        self.conn = pymysql.connect(
            host=config.host,
            port=int(config.port),
            user=config.user,
            password=config.password,
            database=config.database,
            charset=config.charset,
            autocommit=True,
            connect_timeout=int(config.connect_timeout),
        )

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def init_schema(self, timeframe: str = "1d"):
        """初始化表结构（stocks + ema_breakout_signals_{timeframe} + screening_results）"""
        with self.conn.cursor() as cursor:
            # stocks 表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stocks (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    name VARCHAR(255) NULL,
                    exchange VARCHAR(32) NULL,
                    currency VARCHAR(16) NULL,
                    lot_size INT NULL,
                    status VARCHAR(16) NULL,
                    listing_date DATE NULL,
                    delisting_date DATE NULL,
                    sector VARCHAR(128) NULL COMMENT '板块名称',
                    sector_code VARCHAR(64) NULL COMMENT '板块代码',
                    industry VARCHAR(128) NULL COMMENT '行业名称',
                    industry_code VARCHAR(64) NULL COMMENT '行业代码',
                    market_cap DECIMAL(28,2) NULL COMMENT '市值',
                    pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率',
                    pb_ratio DECIMAL(20,6) NULL COMMENT '市净率',
                    source VARCHAR(32) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_stocks_market_code (market, code),
                    KEY idx_stocks_market (market),
                    KEY idx_stocks_code (code),
                    KEY idx_stocks_sector (sector),
                    KEY idx_stocks_industry (industry)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

            # 按 timeframe 创建 EMA 信号表
            self._ensure_ema_table(timeframe)

            # screening_results 表（含 task_id 关联任务，新表创建时包含；已存在的表若缺列需用户自行 ALTER 添加）
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_results (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    task_id VARCHAR(36) NULL,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    name VARCHAR(255) NULL,
                    check_date DATE NOT NULL,
                    is_passed TINYINT(1) NOT NULL DEFAULT 0,
                    filter_summary VARCHAR(512) NULL,
                    filter_details JSON NULL,
                    sector VARCHAR(128) NULL,
                    industry VARCHAR(128) NULL,
                    market_cap DECIMAL(28,2) NULL,
                    pe_ratio DECIMAL(20,6) NULL,
                    close_price DECIMAL(20,6) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_screening_task_market_code (task_id, market, code),
                    KEY idx_screening_task_id (task_id),
                    KEY idx_screening_check_date (check_date),
                    KEY idx_screening_is_passed (is_passed),
                    KEY idx_screening_market_date (market, check_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            self._ensure_screening_results_task_scope(cursor)

            # watchlist_cache 表（自选股缓存，Futu 失败时兜底）
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS watchlist_cache (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    name VARCHAR(255) NULL,
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_watchlist_market_code (market, code),
                    KEY idx_watchlist_market (market)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

            # screening_tasks 表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_tasks (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    task_id VARCHAR(36) NOT NULL,
                    market VARCHAR(8) NOT NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    status VARCHAR(16) NOT NULL DEFAULT 'running',
                    total_count INT NOT NULL DEFAULT 0,
                    completed_count INT NOT NULL DEFAULT 0,
                    current_stock_code VARCHAR(32) NULL,
                    current_stock_name VARCHAR(255) NULL,
                    params_json JSON NULL,
                    check_date DATE NOT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_tasks_task_id (task_id),
                    KEY idx_tasks_status (status),
                    KEY idx_tasks_market_date (market, check_date),
                    KEY idx_tasks_created_at (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            task_alters = [
                ("passed_count", "ALTER TABLE screening_tasks ADD COLUMN passed_count INT NULL AFTER completed_count"),
                ("failed_count", "ALTER TABLE screening_tasks ADD COLUMN failed_count INT NULL AFTER passed_count"),
                ("uploaded_result_scope", "ALTER TABLE screening_tasks ADD COLUMN uploaded_result_scope VARCHAR(32) NULL AFTER failed_count"),
            ]
            for column, alter_sql in task_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM screening_tasks LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass
        self.init_rule_schema()

    def _ensure_screening_results_task_scope(self, cursor):
        """任务结果按 task_id 隔离，避免不同周期/规则链互相覆盖。"""
        try:
            cursor.execute("ALTER TABLE screening_results DROP INDEX uk_screening_market_code_date")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE screening_results "
                "ADD UNIQUE KEY uk_screening_task_market_code (task_id, market, code)"
            )
        except Exception:
            pass

    def init_web_schema(self):
        """初始化 Web、Agent、K 线缓存和 artifact 相关表。"""
        self.init_schema("1d")
        self.init_option_lab_schema()
        self.init_quant_lab_schema()
        self.init_stock_pool_schema()
        self.init_sector_schema()
        self.init_signal_analysis_schema()
        self.init_main_force_risk_schema()
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS web_users (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    username VARCHAR(128) NOT NULL,
                    password_hash VARCHAR(255) NOT NULL,
                    role VARCHAR(32) NOT NULL DEFAULT 'admin',
                    is_active TINYINT(1) NOT NULL DEFAULT 1,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_web_users_username (username),
                    KEY idx_web_users_active (is_active)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='Web 固定登录账号'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS web_sessions (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    session_hash CHAR(64) NOT NULL,
                    user_id BIGINT UNSIGNED NOT NULL,
                    expires_at DATETIME(6) NOT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    last_seen_at DATETIME(6) NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_web_sessions_hash (session_hash),
                    KEY idx_web_sessions_user (user_id),
                    KEY idx_web_sessions_expires (expires_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='Web 服务端 Session'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS web_login_attempts (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    username VARCHAR(128) NOT NULL,
                    ip_address VARCHAR(64) NOT NULL,
                    success TINYINT(1) NOT NULL DEFAULT 0,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    KEY idx_login_attempts_lookup (username, ip_address, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='Web 登录尝试记录'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS data_sync_runs (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    sync_run_id VARCHAR(64) NOT NULL,
                    agent_id VARCHAR(128) NULL,
                    markets JSON NULL,
                    timeframes JSON NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'running',
                    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    finished_at DATETIME(6) NULL,
                    stock_pool_rows INT NOT NULL DEFAULT 0,
                    sector_rows INT NOT NULL DEFAULT 0,
                    kline_rows INT NOT NULL DEFAULT 0,
                    error_message TEXT NULL,
                    metadata_json JSON NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_data_sync_runs_id (sync_run_id),
                    KEY idx_data_sync_status (status),
                    KEY idx_data_sync_started (started_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='本地 Agent 数据同步批次'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_kline_cache (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    bar_time DATETIME(6) NOT NULL,
                    open DECIMAL(20,6) NULL,
                    high DECIMAL(20,6) NULL,
                    low DECIMAL(20,6) NULL,
                    close DECIMAL(20,6) NULL,
                    volume DECIMAL(28,6) NULL,
                    turnover DECIMAL(28,6) NULL,
                    source VARCHAR(32) NOT NULL DEFAULT 'opend_cache',
                    sync_run_id VARCHAR(64) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_kline_cache_bar (market, code, timeframe, bar_time),
                    KEY idx_kline_cache_lookup (market, code, timeframe, bar_time),
                    KEY idx_kline_cache_source (source),
                    KEY idx_kline_cache_sync_run (sync_run_id),
                    KEY idx_kline_cache_updated (updated_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='云端 K 线缓存'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_artifacts (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    artifact_id VARCHAR(64) NOT NULL,
                    task_id VARCHAR(64) NOT NULL,
                    market VARCHAR(8) NULL,
                    artifact_type VARCHAR(32) NOT NULL,
                    file_name VARCHAR(255) NOT NULL,
                    file_path VARCHAR(1024) NOT NULL,
                    content_type VARCHAR(128) NULL,
                    file_size BIGINT NULL,
                    checksum VARCHAR(128) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_screening_artifact_id (artifact_id),
                    KEY idx_screening_artifacts_task (task_id),
                    KEY idx_screening_artifacts_created (created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='筛选导出文件元信息'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS single_stock_runs (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    run_id VARCHAR(64) NOT NULL,
                    user_id BIGINT UNSIGNED NULL,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    normalized_code VARCHAR(32) NOT NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    chain_key VARCHAR(64) NULL,
                    passed TINYINT(1) NOT NULL DEFAULT 0,
                    data_source VARCHAR(32) NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'running',
                    warnings_json JSON NULL,
                    ai_analysis_json JSON NULL,
                    result_json JSON NULL,
                    agent_id VARCHAR(128) NULL,
                    claimed_at DATETIME(6) NULL,
                    heartbeat_at DATETIME(6) NULL,
                    error_message TEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    finished_at DATETIME(6) NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_single_stock_run_id (run_id),
                    KEY idx_single_stock_user (user_id, created_at),
                    KEY idx_single_stock_code (market, normalized_code, created_at)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='单股选股运行记录'
                """
            )
            single_stock_alters = [
                ("chain_key", "ALTER TABLE single_stock_runs ADD COLUMN chain_key VARCHAR(64) NULL AFTER timeframe"),
                ("result_json", "ALTER TABLE single_stock_runs ADD COLUMN result_json JSON NULL AFTER ai_analysis_json"),
                ("agent_id", "ALTER TABLE single_stock_runs ADD COLUMN agent_id VARCHAR(128) NULL AFTER ai_analysis_json"),
                ("claimed_at", "ALTER TABLE single_stock_runs ADD COLUMN claimed_at DATETIME(6) NULL AFTER agent_id"),
                ("heartbeat_at", "ALTER TABLE single_stock_runs ADD COLUMN heartbeat_at DATETIME(6) NULL AFTER claimed_at"),
                ("error_message", "ALTER TABLE single_stock_runs ADD COLUMN error_message TEXT NULL AFTER heartbeat_at"),
            ]
            for column, alter_sql in single_stock_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM single_stock_runs LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS single_stock_rule_details (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    run_id VARCHAR(64) NOT NULL,
                    rule_key VARCHAR(64) NULL,
                    rule_name VARCHAR(128) NOT NULL,
                    rule_type VARCHAR(16) NULL,
                    result VARCHAR(16) NOT NULL,
                    reason TEXT NULL,
                    details_json JSON NULL,
                    display_order INT NOT NULL DEFAULT 0,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    KEY idx_single_rule_run (run_id, display_order)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='单股选股规则明细'
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS web_screening_jobs (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    job_id VARCHAR(64) NOT NULL,
                    user_id BIGINT UNSIGNED NULL,
                    markets JSON NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    status VARCHAR(32) NOT NULL DEFAULT 'queued',
                    task_ids JSON NULL,
                    error_message TEXT NULL,
                    execution_mode VARCHAR(32) NOT NULL DEFAULT 'local_agent',
                    agent_id VARCHAR(128) NULL,
                    claimed_at DATETIME(6) NULL,
                    heartbeat_at DATETIME(6) NULL,
                    options_json JSON NULL,
                    summary_json JSON NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    finished_at DATETIME(6) NULL,
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_web_screening_job_id (job_id),
                    KEY idx_web_screening_jobs_user (user_id, created_at),
                    KEY idx_web_screening_jobs_status (status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='Web 发起的筛选任务组'
                """
            )
            web_job_alters = [
                ("execution_mode", "ALTER TABLE web_screening_jobs ADD COLUMN execution_mode VARCHAR(32) NOT NULL DEFAULT 'local_agent' AFTER error_message"),
                ("agent_id", "ALTER TABLE web_screening_jobs ADD COLUMN agent_id VARCHAR(128) NULL AFTER execution_mode"),
                ("claimed_at", "ALTER TABLE web_screening_jobs ADD COLUMN claimed_at DATETIME(6) NULL AFTER agent_id"),
                ("heartbeat_at", "ALTER TABLE web_screening_jobs ADD COLUMN heartbeat_at DATETIME(6) NULL AFTER claimed_at"),
                ("options_json", "ALTER TABLE web_screening_jobs ADD COLUMN options_json JSON NULL AFTER heartbeat_at"),
                ("summary_json", "ALTER TABLE web_screening_jobs ADD COLUMN summary_json JSON NULL AFTER options_json"),
            ]
            for column, alter_sql in web_job_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM web_screening_jobs LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_run_locks (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    lock_id VARCHAR(64) NOT NULL,
                    run_date DATE NOT NULL,
                    market VARCHAR(8) NOT NULL,
                    timeframe VARCHAR(8) NOT NULL,
                    chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other',
                    pool_scope VARCHAR(255) NOT NULL DEFAULT 'best,major_index,industry_top5,recent_ipo_2y,all_etf',
                    status VARCHAR(32) NOT NULL DEFAULT 'queued',
                    job_id VARCHAR(64) NOT NULL,
                    task_id VARCHAR(64) NULL,
                    agent_id VARCHAR(128) NULL,
                    claimed_at DATETIME(6) NULL,
                    heartbeat_at DATETIME(6) NULL,
                    completed_at DATETIME(6) NULL,
                    error_message TEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe, chain_key, pool_scope),
                    UNIQUE KEY uk_screening_run_lock_id (lock_id),
                    KEY idx_screening_run_lock_job (job_id),
                    KEY idx_screening_run_lock_status (status, run_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='全市场筛选每日分布式锁'
                """
            )
            try:
                cursor.execute("SELECT `chain_key` FROM screening_run_locks LIMIT 1")
            except Exception:
                try:
                    cursor.execute(
                        "ALTER TABLE screening_run_locks "
                        "ADD COLUMN chain_key VARCHAR(64) NOT NULL DEFAULT 'default_zuoyi_and_other' "
                        "AFTER timeframe"
                    )
                except Exception:
                    pass
            try:
                cursor.execute("SELECT `pool_scope` FROM screening_run_locks LIMIT 1")
            except Exception:
                try:
                    cursor.execute(
                        "ALTER TABLE screening_run_locks "
                        f"ADD COLUMN pool_scope VARCHAR(255) NOT NULL DEFAULT '{DEFAULT_POOL_TYPES_TEXT}' "
                        "AFTER chain_key"
                    )
                except Exception:
                    pass
            try:
                cursor.execute("ALTER TABLE screening_run_locks DROP INDEX uk_screening_run_lock_scope")
            except Exception:
                pass
            try:
                cursor.execute(
                    "ALTER TABLE screening_run_locks "
                    "ADD UNIQUE KEY uk_screening_run_lock_scope "
                    "(run_date, market, timeframe, chain_key, pool_scope)"
                )
            except Exception:
                pass

    def _ema_table_name(self, timeframe: str) -> str:
        """获取 EMA 信号表名：ema_breakout_signals_{timeframe}"""
        suffix = _safe_table_suffix(timeframe)
        return f"ema_breakout_signals_{suffix}"

    def _ensure_ema_table(self, timeframe: str):
        """确保指定 timeframe 的 EMA 信号表存在（幂等）"""
        table = self._ema_table_name(timeframe)
        with self.conn.cursor() as cursor:
            cursor.execute(
                f"""
                CREATE TABLE IF NOT EXISTS `{table}` (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场',
                    code VARCHAR(32) NOT NULL COMMENT '股票代码',
                    name VARCHAR(255) NULL COMMENT '股票名称',
                    check_date DATE NOT NULL COMMENT '检查日期',
                    result_type VARCHAR(32) NOT NULL COMMENT '结果类型',
                    is_satisfied TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否满足突破条件',
                    breakout_date DATE NULL COMMENT '突破发生日期',
                    ema10 DECIMAL(20,6) NULL,
                    ema150 DECIMAL(20,6) NULL,
                    close_price DECIMAL(20,6) NULL,
                    data_rows INT NULL,
                    result_desc VARCHAR(255) NULL,
                    sector VARCHAR(128) NULL,
                    industry VARCHAR(128) NULL,
                    market_cap DECIMAL(28,2) NULL,
                    pe_ratio DECIMAL(20,6) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_ema_market_code_date (market, code, check_date),
                    KEY idx_ema_check_date (check_date),
                    KEY idx_ema_is_satisfied (is_satisfied),
                    KEY idx_ema_market_date (market, check_date),
                    KEY idx_ema_sector (sector),
                    KEY idx_ema_industry (industry)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='EMA突破信号 timeframe={timeframe}'
                """
            )

    # ------------------------------------------------------------------
    # stocks
    # ------------------------------------------------------------------

    def stock_count(self, market: str) -> int:
        with self.conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(1) FROM stocks WHERE market=%s", (market,))
            row = cursor.fetchone()
            return int(row[0]) if row else 0

    def upsert_stocks(self, market: str, stocks: Iterable[dict], source: Optional[str] = None):
        rows = []
        for item in stocks:
            rows.append(
                (
                    market,
                    str(item.get("code") or "").strip(),
                    item.get("name"),
                    item.get("exchange"),
                    item.get("currency"),
                    item.get("lot_size"),
                    item.get("status"),
                    item.get("listing_date"),
                    item.get("delisting_date"),
                    item.get("sector"),
                    item.get("sector_code"),
                    item.get("industry"),
                    item.get("industry_code"),
                    item.get("market_cap"),
                    item.get("pe_ratio"),
                    item.get("pb_ratio"),
                    source or item.get("source"),
                )
            )
        rows = [r for r in rows if r[1]]
        if not rows:
            return

        sql = """
            INSERT INTO stocks
                (market, code, name, exchange, currency, lot_size, status,
                 listing_date, delisting_date,
                 sector, sector_code, industry, industry_code,
                 market_cap, pe_ratio, pb_ratio, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                exchange=VALUES(exchange),
                currency=VALUES(currency),
                lot_size=VALUES(lot_size),
                status=VALUES(status),
                listing_date=VALUES(listing_date),
                delisting_date=VALUES(delisting_date),
                sector=COALESCE(VALUES(sector), sector),
                sector_code=COALESCE(VALUES(sector_code), sector_code),
                industry=COALESCE(VALUES(industry), industry),
                industry_code=COALESCE(VALUES(industry_code), industry_code),
                market_cap=COALESCE(VALUES(market_cap), market_cap),
                pe_ratio=COALESCE(VALUES(pe_ratio), pe_ratio),
                pb_ratio=COALESCE(VALUES(pb_ratio), pb_ratio),
                source=VALUES(source)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_stocks(self, market: str, include_fundamentals: bool = False) -> List[dict]:
        with self.conn.cursor() as cursor:
            if include_fundamentals:
                cursor.execute(
                    """SELECT code, name, sector, sector_code, industry, industry_code,
                              market_cap, pe_ratio, pb_ratio
                       FROM stocks WHERE market=%s ORDER BY code""",
                    (market,),
                )
                rows = cursor.fetchall() or []
                return [
                    {
                        "code": r[0], "name": r[1],
                        "sector": r[2], "sector_code": r[3],
                        "industry": r[4], "industry_code": r[5],
                        "market_cap": float(r[6]) if r[6] else None,
                        "pe_ratio": float(r[7]) if r[7] else None,
                        "pb_ratio": float(r[8]) if r[8] else None,
                    }
                    for r in rows
                ]
            else:
                cursor.execute(
                    "SELECT code, name, sector, industry FROM stocks WHERE market=%s ORDER BY code",
                    (market,),
                )
                rows = cursor.fetchall() or []
                return [{"code": r[0], "name": r[1], "sector": r[2], "industry": r[3]} for r in rows]

    def get_stocks_by_codes(self, market: str, codes: List[str], include_fundamentals: bool = True) -> List[dict]:
        """按 code 列表查询股票，用于自选股补全基本面"""
        if not codes:
            return []
        codes = [str(c).strip() for c in codes if str(c).strip()]
        if not codes:
            return []
        placeholders = ",".join(["%s"] * len(codes))
        with self.conn.cursor() as cursor:
            if include_fundamentals:
                cursor.execute(
                    f"""SELECT code, name, sector, sector_code, industry, industry_code,
                               market_cap, pe_ratio, pb_ratio
                        FROM stocks WHERE market=%s AND code IN ({placeholders})""",
                    [market] + codes,
                )
            else:
                cursor.execute(
                    f"SELECT code, name, sector, industry FROM stocks WHERE market=%s AND code IN ({placeholders})",
                    [market] + codes,
                )
            rows = cursor.fetchall() or []
            if include_fundamentals:
                return [
                    {"code": r[0], "name": r[1], "sector": r[2], "sector_code": r[3],
                     "industry": r[4], "industry_code": r[5],
                     "market_cap": float(r[6]) if r[6] else None,
                     "pe_ratio": float(r[7]) if r[7] else None,
                     "pb_ratio": float(r[8]) if r[8] else None}
                    for r in rows
                ]
            return [{"code": r[0], "name": r[1], "sector": r[2], "industry": r[3]} for r in rows]

    def update_stock_sector(self, market, code, sector=None, sector_code=None, industry=None, industry_code=None):
        sql = """UPDATE stocks SET
                    sector=COALESCE(%s,sector), sector_code=COALESCE(%s,sector_code),
                    industry=COALESCE(%s,industry), industry_code=COALESCE(%s,industry_code)
                 WHERE market=%s AND code=%s"""
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (sector, sector_code, industry, industry_code, market, code))

    def batch_update_stock_sectors(self, market: str, sector_data: Iterable[dict]):
        rows = []
        for item in sector_data:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            rows.append((
                item.get("sector"), item.get("sector_code"),
                item.get("industry"), item.get("industry_code"),
                market, code,
            ))
        if not rows:
            return
        sql = """UPDATE stocks SET
                    sector=COALESCE(%s,sector), sector_code=COALESCE(%s,sector_code),
                    industry=COALESCE(%s,industry), industry_code=COALESCE(%s,industry_code)
                 WHERE market=%s AND code=%s"""
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    # ------------------------------------------------------------------
    # EMA 突破信号（按 timeframe 分表）
    # ------------------------------------------------------------------

    def upsert_ema_breakout_signal(
        self,
        timeframe: str,
        market: str,
        code: str,
        check_date: date,
        result_type: str,
        is_satisfied: bool,
        breakout_date: Optional[date] = None,
        ema10: Optional[float] = None,
        ema150: Optional[float] = None,
        close_price: Optional[float] = None,
        data_rows: Optional[int] = None,
        result_desc: Optional[str] = None,
        name: Optional[str] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        market_cap: Optional[float] = None,
        pe_ratio: Optional[float] = None,
    ):
        self._ensure_ema_table(timeframe)
        table = self._ema_table_name(timeframe)
        sql = f"""
            INSERT INTO `{table}`
                (market, code, name, check_date, result_type, is_satisfied,
                 breakout_date, ema10, ema150, close_price, data_rows, result_desc,
                 sector, industry, market_cap, pe_ratio)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=COALESCE(VALUES(name), name),
                result_type=VALUES(result_type),
                is_satisfied=VALUES(is_satisfied),
                breakout_date=VALUES(breakout_date),
                ema10=VALUES(ema10), ema150=VALUES(ema150),
                close_price=VALUES(close_price),
                data_rows=VALUES(data_rows),
                result_desc=VALUES(result_desc),
                sector=COALESCE(VALUES(sector), sector),
                industry=COALESCE(VALUES(industry), industry),
                market_cap=COALESCE(VALUES(market_cap), market_cap),
                pe_ratio=COALESCE(VALUES(pe_ratio), pe_ratio)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                market, code, name, check_date, result_type,
                1 if is_satisfied else 0, breakout_date,
                ema10, ema150, close_price, data_rows, result_desc,
                sector, industry, market_cap, pe_ratio,
            ))

    def get_ema_breakout_signals(
        self,
        timeframe: str,
        market: Optional[str] = None,
        check_date: Optional[date] = None,
        is_satisfied: Optional[bool] = None,
        limit: int = 100,
    ) -> List[dict]:
        self._ensure_ema_table(timeframe)
        table = self._ema_table_name(timeframe)
        query = f"""SELECT market, code, name, check_date, result_type, is_satisfied,
                           breakout_date, ema10, ema150, close_price, data_rows, result_desc,
                           sector, industry, market_cap, pe_ratio
                    FROM `{table}` WHERE 1=1"""
        params: list = []
        if market:
            query += " AND market=%s"; params.append(market)
        if check_date:
            query += " AND check_date=%s"; params.append(check_date)
        if is_satisfied is not None:
            query += " AND is_satisfied=%s"; params.append(1 if is_satisfied else 0)
        query += " ORDER BY check_date DESC, market, code LIMIT %s"
        params.append(limit)

        with self.conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall() or []
            return [
                {
                    "market": r[0], "code": r[1], "name": r[2],
                    "check_date": r[3], "result_type": r[4],
                    "is_satisfied": bool(r[5]), "breakout_date": r[6],
                    "ema10": float(r[7]) if r[7] else None,
                    "ema150": float(r[8]) if r[8] else None,
                    "close_price": float(r[9]) if r[9] else None,
                    "data_rows": r[10], "result_desc": r[11],
                    "sector": r[12], "industry": r[13],
                    "market_cap": float(r[14]) if r[14] else None,
                    "pe_ratio": float(r[15]) if r[15] else None,
                }
                for r in rows
            ]

    # ------------------------------------------------------------------
    # screening_results
    # ------------------------------------------------------------------

    def upsert_screening_results(self, check_date: date, results: Iterable[dict]):
        rows = []
        for item in results:
            fd = item.get("filter_details")
            fd_str = json.dumps(fd, ensure_ascii=False) if isinstance(fd, (dict, list)) else fd
            task_id = item.get("task_id")
            rows.append((
                task_id,
                item.get("market"), str(item.get("code") or "").strip(), item.get("name"),
                check_date, 1 if item.get("is_passed") else 0,
                item.get("filter_summary"), fd_str,
                item.get("sector"), item.get("industry"),
                item.get("market_cap"), item.get("pe_ratio"), item.get("close_price"),
            ))
        rows = [r for r in rows if r[2]]  # code at index 2
        if not rows:
            return
        sql = """
            INSERT INTO screening_results
                (task_id, market, code, name, check_date, is_passed, filter_summary, filter_details,
                 sector, industry, market_cap, pe_ratio, close_price)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                task_id=VALUES(task_id),
                name=VALUES(name), is_passed=VALUES(is_passed),
                filter_summary=VALUES(filter_summary), filter_details=VALUES(filter_details),
                sector=VALUES(sector), industry=VALUES(industry),
                market_cap=VALUES(market_cap), pe_ratio=VALUES(pe_ratio),
                close_price=VALUES(close_price)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def update_screening_result_sectors(self, task_id: str, market: str, rows: Iterable[dict]):
        """补写已生成筛选结果的 sector/industry 字段。"""
        values = []
        for item in rows:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            values.append((
                item.get("sector"),
                item.get("industry"),
                task_id,
                market,
                code,
            ))
        if not values:
            return
        sql = """
            UPDATE screening_results
            SET sector=COALESCE(%s, sector),
                industry=COALESCE(%s, industry)
            WHERE task_id=%s AND market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values)

    def update_screening_result_names(self, task_id: str, market: str, rows: Iterable[dict]):
        """补写已生成筛选结果的展示名称。"""
        values = []
        for item in rows:
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if not code or not name:
                continue
            values.append((name, task_id, market, code))
        if not values:
            return
        sql = """
            UPDATE screening_results
            SET name=%s
            WHERE task_id=%s AND market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values)

    def update_screening_result_main_force_risks(self, task_id: str, market: str, rows: Iterable[dict]):
        """补写已生成筛选结果的主力流出风险摘要。"""
        values = []
        for item in rows:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            values.append((
                item.get("risk_level"),
                item.get("risk_score"),
                item.get("risk_summary"),
                _json_or_none(item.get("triggered_signals")),
                _json_or_none(item.get("provider_status")),
                task_id,
                market,
                code,
            ))
        if not values:
            return
        sql = """
            UPDATE screening_results
            SET main_force_risk_level=%s,
                main_force_risk_score=%s,
                main_force_risk_summary=%s,
                main_force_risk_signals=%s,
                main_force_data_status=%s,
                main_force_risk_updated_at=NOW(6)
            WHERE task_id=%s AND market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values)

    # ------------------------------------------------------------------
    # watchlist_cache
    # ------------------------------------------------------------------

    def upsert_watchlist_cache(self, market: str, stocks: List[dict]) -> None:
        """按市场全量写入自选股缓存（先删后插）"""
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM watchlist_cache WHERE market=%s", (market,))
            if not stocks:
                return
            rows = [
                (market, str(s.get("code") or "").strip(), (s.get("name") or "").strip()[:255])
                for s in stocks
                if str(s.get("code") or "").strip()
            ]
            if not rows:
                return
            cursor.executemany(
                "INSERT INTO watchlist_cache (market, code, name) VALUES (%s,%s,%s)",
                rows,
            )

    def get_watchlist_cache(self, market: str) -> List[dict]:
        """从缓存读取自选股列表"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT code, name FROM watchlist_cache WHERE market=%s ORDER BY code",
                (market,),
            )
            rows = cursor.fetchall() or []
            return [{"code": r[0], "name": r[1] or r[0]} for r in rows]

    # ------------------------------------------------------------------
    # screening_tasks
    # ------------------------------------------------------------------

    def create_screening_task(
        self,
        task_id: str,
        market: str,
        timeframe: str,
        total_count: int,
        params_json: Optional[dict] = None,
        check_date: Optional[date] = None,
    ) -> int:
        """创建筛选任务"""
        if check_date is None:
            check_date = date.today()
        params_str = json.dumps(params_json, ensure_ascii=False) if params_json else None
        sql = """
            INSERT INTO screening_tasks
                (task_id, market, timeframe, status, total_count, completed_count, params_json, check_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (task_id, market, timeframe, "running", total_count, 0, params_str, check_date))
            return cursor.lastrowid

    def update_task_progress(
        self,
        task_id: str,
        completed_count: int,
        current_stock_code: Optional[str] = None,
        current_stock_name: Optional[str] = None,
    ):
        """更新任务进度"""
        sql = """
            UPDATE screening_tasks
            SET completed_count=%s, current_stock_code=%s, current_stock_name=%s
            WHERE task_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (completed_count, current_stock_code, current_stock_name, task_id))

    def update_task_status(self, task_id: str, status: str):
        """更新任务状态"""
        sql = "UPDATE screening_tasks SET status=%s WHERE task_id=%s"
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (status, task_id))

    def get_task_by_id(self, task_id: str) -> Optional[dict]:
        """根据 task_id 获取任务"""
        sql = """
            SELECT task_id, market, timeframe, status, total_count, completed_count,
                   passed_count, failed_count, uploaded_result_scope,
                   current_stock_code, current_stock_name, params_json, check_date,
                   created_at, updated_at
            FROM screening_tasks WHERE task_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            row = cursor.fetchone()
            if not row:
                return None
            params = _decode_json_field(row[11], None)
            params_dict = params if isinstance(params, dict) else {}
            return {
                "task_id": row[0],
                "market": row[1],
                "timeframe": row[2],
                "status": row[3],
                "total_count": int(row[4] or 0),
                "completed_count": int(row[5] or 0),
                "passed_count": int(row[6]) if row[6] is not None else None,
                "failed_count": int(row[7]) if row[7] is not None else None,
                "uploaded_result_scope": row[8],
                "current_stock_code": row[9],
                "current_stock_name": row[10],
                "params_json": params,
                "chain_key": params_dict.get("chain_key"),
                "chain_name": params_dict.get("chain_name"),
                "check_date": str(row[12]) if row[12] else None,
                "created_at": str(row[13]) if row[13] else None,
                "updated_at": str(row[14]) if row[14] else None,
            }

    def get_latest_completed_task(self) -> Optional[dict]:
        """获取最近一次已完成的筛选任务（按 created_at 降序取一条）"""
        sql = """
            SELECT task_id, market, timeframe, status, total_count, completed_count,
                   current_stock_code, current_stock_name, params_json, check_date,
                   created_at, updated_at
            FROM screening_tasks
            WHERE status = 'completed'
            ORDER BY created_at DESC
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql)
            row = cursor.fetchone()
            if not row:
                return None
            params = None
            if row[8]:
                try:
                    params = json.loads(row[8])
                except Exception:
                    params = None
            params_dict = params if isinstance(params, dict) else {}
            return {
                "task_id": row[0],
                "market": row[1],
                "timeframe": row[2],
                "status": row[3],
                "total_count": row[4],
                "completed_count": row[5],
                "current_stock_code": row[6],
                "current_stock_name": row[7],
                "params_json": params,
                "chain_key": params_dict.get("chain_key"),
                "chain_name": params_dict.get("chain_name"),
                "check_date": row[9],
                "created_at": row[10],
                "updated_at": row[11],
            }

    def list_screening_tasks(self, limit: int = 50) -> List[dict]:
        sql = """
            SELECT task_id, market, timeframe, status, total_count, completed_count,
                   passed_count, failed_count, uploaded_result_scope,
                   current_stock_code, current_stock_name, params_json, check_date,
                   created_at, updated_at
            FROM screening_tasks
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            rows = cursor.fetchall() or []
        result = []
        for row in rows:
            params = _decode_json_field(row[11], {})
            result.append({
                "task_id": row[0],
                "market": row[1],
                "timeframe": row[2],
                "status": row[3],
                "total_count": int(row[4] or 0),
                "completed_count": int(row[5] or 0),
                "passed_count": int(row[6]) if row[6] is not None else None,
                "failed_count": int(row[7]) if row[7] is not None else None,
                "uploaded_result_scope": row[8],
                "current_stock_code": row[9],
                "current_stock_name": row[10],
                "params_json": params,
                "chain_key": params.get("chain_key") if isinstance(params, dict) else None,
                "chain_name": params.get("chain_name") if isinstance(params, dict) else None,
                "check_date": str(row[12]) if row[12] else None,
                "created_at": str(row[13]) if row[13] else None,
                "updated_at": str(row[14]) if row[14] else None,
            })
        return result

    def upsert_screening_task_summary(self, item: dict) -> None:
        task_id = str(item.get("task_id") or "").strip()
        if not task_id:
            return
        params_json = item.get("params_json") if "params_json" in item else item.get("params")
        check_date = item.get("check_date") or date.today()
        values = (
            task_id,
            item.get("market"),
            item.get("timeframe"),
            item.get("status") or "completed",
            int(item.get("total_count") or 0),
            int(item.get("completed_count") or item.get("total_count") or 0),
            item.get("passed_count"),
            item.get("failed_count"),
            item.get("uploaded_result_scope") or "passed_only",
            item.get("current_stock_code"),
            item.get("current_stock_name"),
            _json_or_none(params_json or {}),
            check_date,
        )
        sql = """
            INSERT INTO screening_tasks
                (task_id, market, timeframe, status, total_count, completed_count,
                 passed_count, failed_count, uploaded_result_scope,
                 current_stock_code, current_stock_name, params_json, check_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                market=VALUES(market),
                timeframe=VALUES(timeframe),
                status=VALUES(status),
                total_count=VALUES(total_count),
                completed_count=VALUES(completed_count),
                passed_count=VALUES(passed_count),
                failed_count=VALUES(failed_count),
                uploaded_result_scope=VALUES(uploaded_result_scope),
                current_stock_code=VALUES(current_stock_code),
                current_stock_name=VALUES(current_stock_name),
                params_json=VALUES(params_json),
                check_date=VALUES(check_date)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, values)
            if item.get("job_id") and item.get("market"):
                cursor.execute(
                    """
                    UPDATE screening_run_locks
                    SET task_id=%s
                    WHERE job_id=%s AND market=%s AND timeframe=%s
                    """,
                    (task_id, item.get("job_id"), item.get("market"), item.get("timeframe")),
                )

    def count_screening_results_by_task(self, task_id: str, passed_only: Optional[bool] = None) -> int:
        conditions = ["task_id=%s"]
        params: List[Any] = [task_id]
        if passed_only is not None:
            conditions.append("is_passed=%s")
            params.append(1 if passed_only else 0)
        sql = f"SELECT COUNT(*) FROM screening_results WHERE {' AND '.join(conditions)}"
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            row = cursor.fetchone()
        return int(row[0] or 0) if row else 0

    def get_screening_results_by_task(
        self,
        task_id: str,
        limit: int = 1000,
        offset: int = 0,
        passed_only: bool = False,
    ) -> List[dict]:
        conditions = ["task_id=%s"]
        params: List[Any] = [task_id]
        if passed_only:
            conditions.append("is_passed=1")
        sql = """
            SELECT market, code, name, check_date, is_passed, filter_summary,
                   filter_details, sector, industry, market_cap, pe_ratio, close_price,
                   created_at
            FROM screening_results
            WHERE {where_clause}
            ORDER BY is_passed DESC, code ASC
            LIMIT %s OFFSET %s
        """.format(where_clause=" AND ".join(conditions))
        params.extend([int(limit), int(offset)])
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "market": row[0],
                "code": row[1],
                "name": row[2],
                "check_date": str(row[3]) if row[3] else None,
                "is_passed": bool(row[4]),
                "filter_summary": row[5],
                "filter_details": _decode_json_field(row[6], []),
                "sector": row[7],
                "industry": row[8],
                "market_cap": float(row[9]) if row[9] is not None else None,
                "pe_ratio": float(row[10]) if row[10] is not None else None,
                "close_price": float(row[11]) if row[11] is not None else None,
                "created_at": str(row[12]) if row[12] else None,
            }
            for row in rows
        ]

    def get_stock_pool_records_by_codes(self, market: str, codes: List[str]) -> List[dict]:
        codes = [str(code).strip() for code in codes if str(code).strip()]
        if not codes:
            return []
        placeholders = ",".join(["%s"] * len(codes))
        sql = f"""
            SELECT pool_type, code, name, market_cap, price, pe_ratio, turnover, volume,
                   listing_date, days_since_listing, index_code, index_name,
                   industry_code, industry_name, rank_in_industry, extra_data,
                   created_at, updated_at
            FROM stock_pools
            WHERE market=%s AND code IN ({placeholders})
            ORDER BY
                code ASC,
                CASE pool_type
                    WHEN 'best' THEN 0
                    WHEN 'major_index' THEN 1
                    WHEN 'industry_top5' THEN 2
                    WHEN 'recent_ipo_2y' THEN 3
                    WHEN 'all_etf' THEN 4
                    ELSE 5
                END,
                updated_at DESC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, [market] + codes)
            rows = cursor.fetchall() or []
        result = []
        for row in rows:
            result.append({
                "pool_type": row[0],
                "code": row[1],
                "name": row[2],
                "market_cap": float(row[3]) if row[3] is not None else None,
                "price": float(row[4]) if row[4] is not None else None,
                "pe_ratio": float(row[5]) if row[5] is not None else None,
                "turnover": float(row[6]) if row[6] is not None else None,
                "volume": int(row[7]) if row[7] is not None else None,
                "listing_date": str(row[8]) if row[8] else None,
                "days_since_listing": int(row[9]) if row[9] is not None else None,
                "index_code": row[10],
                "index_name": row[11],
                "industry_code": row[12],
                "industry_name": row[13],
                "rank_in_industry": int(row[14]) if row[14] is not None else None,
                "extra_data": _decode_json_field(row[15], {}),
                "created_at": str(row[16]) if row[16] else None,
                "updated_at": str(row[17]) if row[17] else None,
            })
        return result

    # ------------------------------------------------------------------
    # Web auth
    # ------------------------------------------------------------------

    def upsert_web_user(self, username: str, password: str, role: str = "admin", is_active: bool = True) -> int:
        username = str(username or "").strip()
        if not username:
            raise ValueError("username is required")
        password_hash = hash_password(password)
        sql = """
            INSERT INTO web_users (username, password_hash, role, is_active)
            VALUES (%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                password_hash=VALUES(password_hash),
                role=VALUES(role),
                is_active=VALUES(is_active)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (username, password_hash, role, 1 if is_active else 0))
            return int(cursor.lastrowid or 0)

    def get_web_user_by_username(self, username: str) -> Optional[dict]:
        sql = """
            SELECT id, username, password_hash, role, is_active, created_at, updated_at
            FROM web_users WHERE username=%s LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (username,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "id": int(row[0]),
            "username": row[1],
            "password_hash": row[2],
            "role": row[3],
            "is_active": bool(row[4]),
            "created_at": str(row[5]) if row[5] else None,
            "updated_at": str(row[6]) if row[6] else None,
        }

    def record_login_attempt(self, username: str, ip_address: str, success: bool) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO web_login_attempts (username, ip_address, success) VALUES (%s,%s,%s)",
                (username, ip_address, 1 if success else 0),
            )

    def count_recent_failed_logins(self, username: str, ip_address: str, window_minutes: int = 15) -> int:
        since = _utcnow() - timedelta(minutes=window_minutes)
        sql = """
            SELECT COUNT(1) FROM web_login_attempts
            WHERE username=%s AND ip_address=%s AND success=0 AND created_at >= %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (username, ip_address, since))
            row = cursor.fetchone()
        return int(row[0] or 0) if row else 0

    def create_web_session(self, user_id: int, ttl_hours: int = 24) -> str:
        token = secrets.token_urlsafe(48)
        expires_at = _utcnow() + timedelta(hours=ttl_hours)
        with self.conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO web_sessions (session_hash, user_id, expires_at, last_seen_at) VALUES (%s,%s,%s,%s)",
                (session_hash(token), int(user_id), expires_at, _utcnow()),
            )
        return token

    def get_user_by_session_token(self, token: str) -> Optional[dict]:
        sql = """
            SELECT u.id, u.username, u.role, u.is_active, s.expires_at
            FROM web_sessions s
            JOIN web_users u ON u.id=s.user_id
            WHERE s.session_hash=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (session_hash(token),))
            row = cursor.fetchone()
            if not row:
                return None
            expires_at = row[4]
            if expires_at and expires_at <= _utcnow():
                return None
            cursor.execute("UPDATE web_sessions SET last_seen_at=%s WHERE session_hash=%s", (_utcnow(), session_hash(token)))
        if not bool(row[3]):
            return None
        return {"id": int(row[0]), "username": row[1], "role": row[2], "is_active": bool(row[3])}

    def delete_web_session(self, token: str) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM web_sessions WHERE session_hash=%s", (session_hash(token),))

    # ------------------------------------------------------------------
    # Web screening jobs and artifacts
    # ------------------------------------------------------------------

    def create_web_screening_job(
        self,
        job_id: str,
        user_id: Optional[int],
        markets: List[str],
        timeframe: str,
        options: Optional[dict] = None,
    ) -> None:
        sql = """
            INSERT INTO web_screening_jobs
                (job_id, user_id, markets, timeframe, status, execution_mode, options_json)
            VALUES (%s,%s,%s,%s,'queued','local_agent',%s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (job_id, user_id, _json_or_none(markets), timeframe, _json_or_none(options or {})))

    def update_web_screening_job(
        self,
        job_id: str,
        status: str,
        task_ids: Optional[List[str]] = None,
        error_message: Optional[str] = None,
        finished: bool = False,
        summary: Optional[dict] = None,
    ) -> None:
        sql = """
            UPDATE web_screening_jobs
            SET status=%s, task_ids=COALESCE(%s, task_ids), error_message=%s,
                summary_json=COALESCE(%s, summary_json),
                finished_at=CASE WHEN %s=1 THEN %s ELSE finished_at END
            WHERE job_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                status,
                _json_or_none(task_ids) if task_ids is not None else None,
                error_message,
                _json_or_none(summary) if summary is not None else None,
                1 if finished else 0,
                _utcnow(),
                job_id,
            ))

    def list_web_screening_jobs(self, limit: int = 50) -> List[dict]:
        sql = """
            SELECT job_id, user_id, markets, timeframe, status, task_ids,
                   error_message, execution_mode, agent_id, claimed_at, heartbeat_at,
                   options_json, summary_json, created_at, updated_at, finished_at
            FROM web_screening_jobs
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            rows = cursor.fetchall() or []
        return [
            {
                "job_id": row[0],
                "user_id": row[1],
                "markets": _decode_json_field(row[2], []),
                "timeframe": row[3],
                "status": row[4],
                "task_ids": _decode_json_field(row[5], []),
                "error_message": row[6],
                "execution_mode": row[7],
                "agent_id": row[8],
                "claimed_at": str(row[9]) if row[9] else None,
                "heartbeat_at": str(row[10]) if row[10] else None,
                "options": _decode_json_field(row[11], {}),
                "chain_key": _decode_json_field(row[11], {}).get("chain_key"),
                "chain_timeframe": _decode_json_field(row[11], {}).get("chain_timeframe"),
                "chain_name": _decode_json_field(row[11], {}).get("chain_name"),
                "summary": _decode_json_field(row[12], {}),
                "created_at": str(row[13]) if row[13] else None,
                "updated_at": str(row[14]) if row[14] else None,
                "finished_at": str(row[15]) if row[15] else None,
            }
            for row in rows
        ]

    def get_web_screening_job(self, job_id: str) -> Optional[dict]:
        sql = """
            SELECT job_id, user_id, markets, timeframe, status, task_ids,
                   error_message, execution_mode, agent_id, claimed_at, heartbeat_at,
                   options_json, summary_json, created_at, updated_at, finished_at
            FROM web_screening_jobs
            WHERE job_id=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (job_id,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "job_id": row[0],
            "user_id": row[1],
            "markets": _decode_json_field(row[2], []),
            "timeframe": row[3],
            "status": row[4],
            "task_ids": _decode_json_field(row[5], []),
            "error_message": row[6],
            "execution_mode": row[7],
            "agent_id": row[8],
            "claimed_at": str(row[9]) if row[9] else None,
            "heartbeat_at": str(row[10]) if row[10] else None,
            "options": _decode_json_field(row[11], {}),
            "chain_key": _decode_json_field(row[11], {}).get("chain_key"),
            "chain_timeframe": _decode_json_field(row[11], {}).get("chain_timeframe"),
            "chain_name": _decode_json_field(row[11], {}).get("chain_name"),
            "summary": _decode_json_field(row[12], {}),
            "created_at": str(row[13]) if row[13] else None,
            "updated_at": str(row[14]) if row[14] else None,
            "finished_at": str(row[15]) if row[15] else None,
        }

    def get_screening_run_locks(
        self,
        run_date: date,
        markets: List[str],
        timeframe: str,
        chain_key: Optional[str] = None,
        pool_scope: Optional[str] = None,
    ) -> List[dict]:
        if not markets:
            return []
        placeholders = ",".join(["%s"] * len(markets))
        chain_condition = " AND chain_key=%s" if chain_key else ""
        pool_condition = " AND pool_scope=%s" if pool_scope else ""
        sql = f"""
            SELECT lock_id, run_date, market, timeframe, chain_key, pool_scope, status, job_id, task_id,
                   agent_id, claimed_at, heartbeat_at, completed_at, error_message
            FROM screening_run_locks
            WHERE run_date=%s AND timeframe=%s AND market IN ({placeholders}){chain_condition}{pool_condition}
            ORDER BY market
        """
        params: List[Any] = [run_date, timeframe, *markets]
        if chain_key:
            params.append(chain_key)
        if pool_scope:
            params.append(pool_scope)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "lock_id": row[0],
                "run_date": str(row[1]) if row[1] else None,
                "market": row[2],
                "timeframe": row[3],
                "chain_key": row[4],
                "pool_scope": row[5],
                "status": row[6],
                "job_id": row[7],
                "task_id": row[8],
                "agent_id": row[9],
                "claimed_at": str(row[10]) if row[10] else None,
                "heartbeat_at": str(row[11]) if row[11] else None,
                "completed_at": str(row[12]) if row[12] else None,
                "error_message": row[13],
            }
            for row in rows
        ]

    def create_screening_run_locks(
        self,
        job_id: str,
        run_date: date,
        markets: List[str],
        timeframe: str,
        chain_key: str = DEFAULT_RULE_CHAIN_KEY,
        pool_scope: str = DEFAULT_POOL_TYPES_TEXT,
    ) -> None:
        rows = [
            (secrets.token_hex(16), run_date, market, timeframe, chain_key, pool_scope, "queued", job_id)
            for market in markets
        ]
        if not rows:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO screening_run_locks
                    (lock_id, run_date, market, timeframe, chain_key, pool_scope, status, job_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                rows,
            )

    def list_pending_agent_jobs(self, limit: int = 5, stale_after_seconds: int = 1800) -> List[dict]:
        self.expire_stale_agent_locks(stale_after_seconds)
        sql = """
            SELECT DISTINCT j.job_id, j.user_id, j.markets, j.timeframe, j.status,
                   j.options_json, j.created_at
            FROM web_screening_jobs j
            JOIN screening_run_locks l ON l.job_id=j.job_id
            WHERE l.status IN ('queued','expired')
              AND j.status IN ('queued','running')
            ORDER BY j.created_at ASC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            screening_rows = cursor.fetchall() or []
            cursor.execute(
                """
                SELECT j.job_id, j.user_id, j.markets, j.timeframe, j.status,
                       j.options_json, j.created_at
                FROM web_screening_jobs j
                WHERE j.status IN ('queued','expired')
                  AND JSON_UNQUOTE(JSON_EXTRACT(j.options_json, '$.job_kind')) = 'custom_list'
                ORDER BY j.created_at ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            custom_rows = cursor.fetchall() or []
            cursor.execute(
                """
                SELECT run_id, user_id, market, code, normalized_code, timeframe,
                       chain_key, status, created_at
                FROM single_stock_runs
                WHERE status IN ('queued','expired')
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (int(limit),),
            )
            single_rows = cursor.fetchall() or []
        jobs = [
            {
                "job_type": "screening",
                "job_id": row[0],
                "user_id": row[1],
                "markets": _decode_json_field(row[2], []),
                "timeframe": row[3],
                "status": row[4],
                "options": _decode_json_field(row[5], {}),
                "chain_key": _decode_json_field(row[5], {}).get("chain_key"),
                "chain_timeframe": _decode_json_field(row[5], {}).get("chain_timeframe"),
                "chain_name": _decode_json_field(row[5], {}).get("chain_name"),
                "created_at": str(row[6]) if row[6] else None,
            }
            for row in screening_rows
        ]
        jobs.extend(
            {
                "job_type": "screening",
                "job_id": row[0],
                "user_id": row[1],
                "markets": _decode_json_field(row[2], []),
                "timeframe": row[3],
                "status": row[4],
                "options": _decode_json_field(row[5], {}),
                "chain_key": _decode_json_field(row[5], {}).get("chain_key"),
                "chain_timeframe": _decode_json_field(row[5], {}).get("chain_timeframe"),
                "chain_name": _decode_json_field(row[5], {}).get("chain_name"),
                "created_at": str(row[6]) if row[6] else None,
            }
            for row in custom_rows
        )
        jobs.extend(
            {
                "job_type": "single_stock",
                "job_id": row[0],
                "run_id": row[0],
                "user_id": row[1],
                "market": row[2],
                "code": row[3],
                "normalized_code": row[4],
                "timeframe": row[5],
                "chain_key": row[6],
                "status": row[7],
                "created_at": str(row[8]) if row[8] else None,
            }
            for row in single_rows
        )
        return jobs[: int(limit)]

    def claim_agent_job(self, job_id: str, agent_id: str) -> Optional[dict]:
        now = _utcnow()
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE screening_run_locks
                SET status='running', agent_id=%s, claimed_at=COALESCE(claimed_at,%s),
                    heartbeat_at=%s, error_message=NULL
                WHERE job_id=%s AND status IN ('queued','expired')
                """,
                (agent_id, now, now, job_id),
            )
            if cursor.rowcount:
                cursor.execute(
                    """
                    UPDATE web_screening_jobs
                    SET status='running', agent_id=%s, claimed_at=COALESCE(claimed_at,%s),
                        heartbeat_at=%s, error_message=NULL
                    WHERE job_id=%s
                    """,
                    (agent_id, now, now, job_id),
                )
                return self.get_web_screening_job(job_id)

            cursor.execute(
                """
                UPDATE web_screening_jobs
                SET status='running', agent_id=%s, claimed_at=COALESCE(claimed_at,%s),
                    heartbeat_at=%s, error_message=NULL
                WHERE job_id=%s
                  AND status IN ('queued','expired')
                  AND JSON_UNQUOTE(JSON_EXTRACT(options_json, '$.job_kind')) = 'custom_list'
                """,
                (agent_id, now, now, job_id),
            )
            if cursor.rowcount:
                return self.get_web_screening_job(job_id)

            cursor.execute(
                """
                UPDATE single_stock_runs
                SET status='running', agent_id=%s, claimed_at=COALESCE(claimed_at,%s),
                    heartbeat_at=%s, error_message=NULL
                WHERE run_id=%s AND status IN ('queued','expired')
                """,
                (agent_id, now, now, job_id),
            )
            if cursor.rowcount:
                return self.get_single_stock_run(job_id)
        return None

    def heartbeat_agent_job(self, job_id: str, agent_id: str, progress: Optional[dict] = None) -> None:
        now = _utcnow()
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE screening_run_locks
                SET heartbeat_at=%s
                WHERE job_id=%s AND agent_id=%s AND status='running'
                """,
                (now, job_id, agent_id),
            )
            cursor.execute(
                """
                UPDATE web_screening_jobs
                SET heartbeat_at=%s, summary_json=COALESCE(%s, summary_json)
                WHERE job_id=%s AND agent_id=%s AND status='running'
                """,
                (now, _json_or_none(progress) if progress else None, job_id, agent_id),
            )
            cursor.execute(
                """
                UPDATE single_stock_runs
                SET heartbeat_at=%s
                WHERE run_id=%s AND agent_id=%s AND status='running'
                """,
                (now, job_id, agent_id),
            )

    def complete_agent_screening_job(
        self,
        job_id: str,
        status: str,
        task_ids: Optional[List[str]] = None,
        summary: Optional[dict] = None,
        error_message: Optional[str] = None,
    ) -> None:
        now = _utcnow()
        with self.conn.cursor() as cursor:
            market_statuses = (summary or {}).get("market_statuses") if isinstance(summary, dict) else None
            if isinstance(market_statuses, dict) and market_statuses:
                for market, item in market_statuses.items():
                    item = item if isinstance(item, dict) else {}
                    lock_status = "completed" if item.get("status") == "completed" else "failed"
                    cursor.execute(
                        """
                        UPDATE screening_run_locks
                        SET status=%s, task_id=COALESCE(%s, task_id), completed_at=%s,
                            error_message=%s
                        WHERE job_id=%s AND market=%s
                        """,
                        (lock_status, item.get("task_id"), now, item.get("error_message"), job_id, market),
                    )
            else:
                lock_status = "completed" if status == "completed" else "failed"
                cursor.execute(
                    """
                    UPDATE screening_run_locks
                    SET status=%s, completed_at=%s, error_message=%s
                    WHERE job_id=%s
                    """,
                    (lock_status, now, error_message, job_id),
                )
        self.update_web_screening_job(
            job_id,
            status,
            task_ids=task_ids,
            error_message=error_message,
            finished=True,
            summary=summary,
        )

    def expire_stale_agent_locks(self, stale_after_seconds: int = 1800) -> int:
        cutoff = _utcnow() - timedelta(seconds=max(60, int(stale_after_seconds)))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE screening_run_locks
                SET status='expired', error_message='Agent heartbeat timeout'
                WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < %s
                """,
                (cutoff,),
            )
            expired_locks = cursor.rowcount
            cursor.execute(
                """
                UPDATE web_screening_jobs
                SET status='queued', error_message='Agent heartbeat timeout'
                WHERE status='running'
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < %s
                  AND job_id IN (
                      SELECT job_id FROM screening_run_locks WHERE status='expired'
                  )
                """,
                (cutoff,),
            )
            cursor.execute(
                """
                UPDATE single_stock_runs
                SET status='expired', error_message='Agent heartbeat timeout'
                WHERE status='running' AND heartbeat_at IS NOT NULL AND heartbeat_at < %s
                """,
                (cutoff,),
            )
            expired_single_runs = cursor.rowcount
            cursor.execute(
                """
                UPDATE web_screening_jobs
                SET status='expired', error_message='Agent heartbeat timeout'
                WHERE status='running'
                  AND heartbeat_at IS NOT NULL
                  AND heartbeat_at < %s
                  AND JSON_UNQUOTE(JSON_EXTRACT(options_json, '$.job_kind')) = 'custom_list'
                """,
                (cutoff,),
            )
            expired_custom_jobs = cursor.rowcount
            return int(expired_locks + expired_single_runs + expired_custom_jobs)

    def create_screening_artifact(self, item: dict) -> str:
        artifact_id = item.get("artifact_id") or secrets.token_hex(16)
        sql = """
            INSERT INTO screening_artifacts
                (artifact_id, task_id, market, artifact_type, file_name, file_path,
                 content_type, file_size, checksum)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                file_name=VALUES(file_name),
                file_path=VALUES(file_path),
                file_size=VALUES(file_size),
                checksum=VALUES(checksum)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                artifact_id,
                item.get("task_id"),
                item.get("market"),
                item.get("artifact_type"),
                item.get("file_name"),
                item.get("file_path"),
                item.get("content_type"),
                item.get("file_size"),
                item.get("checksum"),
            ))
        return artifact_id

    def list_screening_artifacts(self, task_id: Optional[str] = None, limit: int = 100) -> List[dict]:
        params: list = []
        sql = """
            SELECT artifact_id, task_id, market, artifact_type, file_name, file_path,
                   content_type, file_size, checksum, created_at
            FROM screening_artifacts
            WHERE 1=1
        """
        if task_id:
            sql += " AND task_id=%s"
            params.append(task_id)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(int(limit))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "artifact_id": row[0],
                "task_id": row[1],
                "market": row[2],
                "artifact_type": row[3],
                "file_name": row[4],
                "file_path": row[5],
                "content_type": row[6],
                "file_size": int(row[7]) if row[7] is not None else None,
                "checksum": row[8],
                "created_at": str(row[9]) if row[9] else None,
            }
            for row in rows
        ]

    def get_screening_artifact(self, artifact_id: str) -> Optional[dict]:
        sql = """
            SELECT artifact_id, task_id, market, artifact_type, file_name, file_path,
                   content_type, file_size, checksum, created_at
            FROM screening_artifacts
            WHERE artifact_id=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (artifact_id,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "artifact_id": row[0],
            "task_id": row[1],
            "market": row[2],
            "artifact_type": row[3],
            "file_name": row[4],
            "file_path": row[5],
            "content_type": row[6],
            "file_size": int(row[7]) if row[7] is not None else None,
            "checksum": row[8],
            "created_at": str(row[9]) if row[9] else None,
        }

    # ------------------------------------------------------------------
    # Agent sync and K line cache
    # ------------------------------------------------------------------

    def create_data_sync_run(
        self,
        sync_run_id: str,
        agent_id: Optional[str],
        markets: List[str],
        timeframes: List[str],
        metadata: Optional[dict] = None,
    ) -> None:
        sql = """
            INSERT INTO data_sync_runs
                (sync_run_id, agent_id, markets, timeframes, status, metadata_json)
            VALUES (%s,%s,%s,%s,'running',%s)
            ON DUPLICATE KEY UPDATE
                agent_id=VALUES(agent_id),
                markets=VALUES(markets),
                timeframes=VALUES(timeframes),
                status='running',
                metadata_json=VALUES(metadata_json)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                sync_run_id,
                agent_id,
                _json_or_none(markets),
                _json_or_none(timeframes),
                _json_or_none(metadata or {}),
            ))

    def complete_data_sync_run(
        self,
        sync_run_id: str,
        status: str,
        error_message: Optional[str] = None,
        stock_pool_rows: Optional[int] = None,
        sector_rows: Optional[int] = None,
        kline_rows: Optional[int] = None,
    ) -> None:
        sql = """
            UPDATE data_sync_runs
            SET status=%s,
                finished_at=%s,
                error_message=%s,
                stock_pool_rows=COALESCE(%s, stock_pool_rows),
                sector_rows=COALESCE(%s, sector_rows),
                kline_rows=COALESCE(%s, kline_rows)
            WHERE sync_run_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                status,
                _utcnow(),
                error_message,
                stock_pool_rows,
                sector_rows,
                kline_rows,
                sync_run_id,
            ))

    def latest_data_sync_runs(self, limit: int = 10) -> List[dict]:
        sql = """
            SELECT sync_run_id, agent_id, markets, timeframes, status, started_at,
                   finished_at, stock_pool_rows, sector_rows, kline_rows, error_message
            FROM data_sync_runs
            ORDER BY started_at DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            rows = cursor.fetchall() or []
        return [
            {
                "sync_run_id": row[0],
                "agent_id": row[1],
                "markets": _decode_json_field(row[2], []),
                "timeframes": _decode_json_field(row[3], []),
                "status": row[4],
                "started_at": str(row[5]) if row[5] else None,
                "finished_at": str(row[6]) if row[6] else None,
                "stock_pool_rows": int(row[7] or 0),
                "sector_rows": int(row[8] or 0),
                "kline_rows": int(row[9] or 0),
                "error_message": row[10],
            }
            for row in rows
        ]

    def upsert_kline_cache(self, rows: Iterable[dict]) -> int:
        values = []
        for item in rows:
            market = str(item.get("market") or "").strip()
            code = str(item.get("code") or "").strip()
            timeframe = str(item.get("timeframe") or "").strip()
            bar_time = item.get("bar_time") or item.get("date")
            if not (market and code and timeframe and bar_time):
                continue
            values.append((
                market,
                code,
                timeframe,
                bar_time,
                item.get("open"),
                item.get("high"),
                item.get("low"),
                item.get("close"),
                item.get("volume"),
                item.get("turnover"),
                item.get("source") or "opend_cache",
                item.get("sync_run_id"),
            ))
        if not values:
            return 0
        sql = """
            INSERT INTO stock_kline_cache
                (market, code, timeframe, bar_time, open, high, low, close,
                 volume, turnover, source, sync_run_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open),
                high=VALUES(high),
                low=VALUES(low),
                close=VALUES(close),
                volume=VALUES(volume),
                turnover=VALUES(turnover),
                source=VALUES(source),
                sync_run_id=VALUES(sync_run_id)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values)
        return len(values)

    def get_kline_cache(self, market: str, code: str, timeframe: str, max_count: int = 500) -> pd.DataFrame:
        sql = """
            SELECT bar_time, open, high, low, close, volume, turnover, source
            FROM stock_kline_cache
            WHERE market=%s AND code=%s AND timeframe=%s
            ORDER BY bar_time DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, code, timeframe, int(max_count)))
            rows = cursor.fetchall() or []
        if not rows:
            return pd.DataFrame()
        data = [
            {
                "date": row[0],
                "open": float(row[1]) if row[1] is not None else None,
                "high": float(row[2]) if row[2] is not None else None,
                "low": float(row[3]) if row[3] is not None else None,
                "close": float(row[4]) if row[4] is not None else None,
                "volume": float(row[5]) if row[5] is not None else None,
                "turnover": float(row[6]) if row[6] is not None else None,
                "source": row[7],
            }
            for row in rows
        ]
        return pd.DataFrame(data).sort_values("date").reset_index(drop=True)

    def prune_kline_cache(self, market: str, code: str, timeframe: str, max_bars: int = 500) -> None:
        sql = """
            DELETE FROM stock_kline_cache
            WHERE market=%s AND code=%s AND timeframe=%s
              AND id NOT IN (
                  SELECT id FROM (
                      SELECT id
                      FROM stock_kline_cache
                      WHERE market=%s AND code=%s AND timeframe=%s
                      ORDER BY bar_time DESC
                      LIMIT %s
                  ) keep_rows
              )
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, code, timeframe, market, code, timeframe, int(max_bars)))

    # ------------------------------------------------------------------
    # Market Intel
    # ------------------------------------------------------------------

    def init_market_intel_schema(self) -> None:
        schema_path = Path(__file__).resolve().parent / "sql" / "017_market_intel.sql"
        sql_text = schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

    def upsert_market_intel_items(self, items: Iterable[dict]) -> None:
        rows = []
        now = _utcnow()
        for item in items:
            scope_type = str(item.get("scope_type") or "stock").strip()
            market = str(item.get("market") or "").strip()
            provider = str(item.get("provider") or item.get("source") or "").strip()
            dedupe_key = str(
                item.get("dedupe_key") or item.get("source_id") or item.get("url") or item.get("title") or ""
            ).strip()
            if not (scope_type and market and provider and dedupe_key):
                continue
            fetched_at = _mysql_datetime_or_none(item.get("fetched_at")) or now
            rows.append((
                scope_type,
                market,
                str(item.get("code") or "").strip(),
                str(item.get("source") or provider).strip(),
                provider,
                str(item.get("item_type") or "other").strip(),
                str(item.get("title") or "").strip(),
                item.get("summary"),
                str(item.get("url") or "").strip(),
                _mysql_datetime_or_none(item.get("published_at")),
                fetched_at,
                _mysql_datetime_or_none(item.get("expires_at")) or fetched_at,
                1 if item.get("is_stale") else 0,
                dedupe_key,
                _json_or_none(item.get("raw_json")),
            ))
        if not rows:
            return
        sql = """
            INSERT INTO market_intel_items
                (scope_type, market, code, source, provider, item_type, title, summary, url,
                 published_at, fetched_at, expires_at, is_stale, dedupe_key, raw_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                source=VALUES(source),
                item_type=VALUES(item_type),
                title=VALUES(title),
                summary=VALUES(summary),
                url=VALUES(url),
                published_at=VALUES(published_at),
                fetched_at=VALUES(fetched_at),
                expires_at=VALUES(expires_at),
                is_stale=VALUES(is_stale),
                raw_json=VALUES(raw_json)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def list_market_intel_items(
        self,
        *,
        scope_type: str = "stock",
        market: str,
        code: str = "",
        include_stale: bool = True,
        limit: int = 200,
    ) -> List[dict]:
        sql = """
            SELECT scope_type, market, code, source, provider, item_type, title, summary, url,
                   published_at, raw_json, fetched_at, expires_at, is_stale, dedupe_key
            FROM market_intel_items
            WHERE scope_type=%s AND market=%s AND code=%s
        """
        params: List[Any] = [scope_type, market, code or ""]
        if not include_stale:
            sql += " AND is_stale=0"
        sql += " ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT %s"
        params.append(max(1, int(limit)))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall() or []
        return [
            {
                "scope_type": row[0],
                "market": row[1],
                "code": row[2],
                "source": row[3],
                "provider": row[4],
                "item_type": row[5],
                "title": row[6],
                "summary": row[7],
                "url": row[8],
                "published_at": row[9],
                "raw_json": _decode_json_field(row[10], {}),
                "fetched_at": row[11],
                "expires_at": row[12],
                "is_stale": bool(row[13]),
                "dedupe_key": row[14],
            }
            for row in rows
        ]

    def upsert_market_intel_bundle(self, row: dict) -> None:
        sql = """
            INSERT INTO market_intel_bundles
                (scope_type, market, code, bundle_json, freshness_status, source_status_json)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                bundle_json=VALUES(bundle_json),
                freshness_status=VALUES(freshness_status),
                source_status_json=VALUES(source_status_json)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                row["scope_type"],
                row["market"],
                row.get("code") or "",
                _json_or_none(row.get("bundle_json") or {}),
                row.get("freshness_status") or "empty",
                _json_or_none(row.get("source_status_json") or {}),
            ))

    def get_market_intel_bundle(self, scope_type: str, market: str, code: str = "") -> Optional[dict]:
        sql = """
            SELECT scope_type, market, code, bundle_json, freshness_status, source_status_json, updated_at
            FROM market_intel_bundles
            WHERE scope_type=%s AND market=%s AND code=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (scope_type, market, code or ""))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "scope_type": row[0],
            "market": row[1],
            "code": row[2],
            "bundle_json": _decode_json_field(row[3], {}),
            "freshness_status": row[4],
            "source_status_json": _decode_json_field(row[5], {}),
            "updated_at": row[6],
        }

    def insert_market_intel_provider_run(self, row: dict) -> None:
        now = _utcnow()
        sql = """
            INSERT INTO market_intel_provider_runs
                (provider, scope_type, market, code, status, error_message, duration_ms,
                 item_count, raw_json, started_at, finished_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                row["provider"],
                row.get("scope_type") or "stock",
                row["market"],
                row.get("code") or "",
                row.get("status") or "failed",
                row.get("error_message"),
                int(row.get("duration_ms") or 0),
                int(row.get("item_count") or 0),
                _json_or_none(row.get("raw_json")),
                _mysql_datetime_or_none(row.get("started_at")) or now,
                _mysql_datetime_or_none(row.get("finished_at")) or now,
            ))

    def list_market_intel_provider_runs(
        self,
        provider: Optional[str] = None,
        market: Optional[str] = None,
        code: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        sql = """
            SELECT provider, scope_type, market, code, status, error_message, duration_ms,
                   item_count, raw_json, started_at, finished_at
            FROM market_intel_provider_runs
            WHERE 1=1
        """
        params: List[Any] = []
        if provider:
            sql += " AND provider=%s"
            params.append(provider)
        if market:
            sql += " AND market=%s"
            params.append(market)
        if code is not None:
            sql += " AND code=%s"
            params.append(code or "")
        if status:
            sql += " AND status=%s"
            params.append(status)
        sql += " ORDER BY started_at DESC, id DESC LIMIT %s"
        params.append(max(1, int(limit)))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall() or []
        return [
            {
                "provider": row[0],
                "scope_type": row[1],
                "market": row[2],
                "code": row[3],
                "status": row[4],
                "error_message": row[5],
                "duration_ms": row[6],
                "item_count": row[7],
                "raw_json": _decode_json_field(row[8], None),
                "started_at": row[9],
                "finished_at": row[10],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Option Lab
    # ------------------------------------------------------------------

    def init_quant_lab_schema(self) -> None:
        schema_path = Path(__file__).parent / "sql" / "014_quant_lab.sql"
        sql_text = schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            self._ensure_quant_lab_schema_migrations(cursor)

    def _ensure_quant_lab_schema_migrations(self, cursor) -> None:
        try:
            cursor.execute("SELECT progress_pct FROM quant_backtest_runs LIMIT 1")
        except Exception:
            cursor.execute("ALTER TABLE quant_backtest_runs ADD COLUMN progress_pct INT NOT NULL DEFAULT 0 AFTER status")
        try:
            cursor.execute("SELECT current_stage FROM quant_backtest_runs LIMIT 1")
        except Exception:
            cursor.execute("ALTER TABLE quant_backtest_runs ADD COLUMN current_stage VARCHAR(64) NULL AFTER progress_pct")
        try:
            cursor.execute("SELECT chart_json FROM quant_backtest_runs LIMIT 1")
        except Exception:
            cursor.execute("ALTER TABLE quant_backtest_runs ADD COLUMN chart_json JSON NULL AFTER metrics_json")
        try:
            cursor.execute("SELECT progress_logs_json FROM quant_backtest_runs LIMIT 1")
        except Exception:
            cursor.execute(
                "ALTER TABLE quant_backtest_runs ADD COLUMN progress_logs_json JSON NULL AFTER warnings_json"
            )

    def create_quant_backtest_run(self, row: dict) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO quant_backtest_runs
                    (run_id, user_id, status, progress_pct, current_stage, request_json,
                     rule_chain_snapshot_json, warnings_json, progress_logs_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    user_id=VALUES(user_id),
                    status=VALUES(status),
                    progress_pct=VALUES(progress_pct),
                    current_stage=VALUES(current_stage),
                    request_json=VALUES(request_json),
                    rule_chain_snapshot_json=VALUES(rule_chain_snapshot_json),
                    warnings_json=VALUES(warnings_json),
                    progress_logs_json=VALUES(progress_logs_json)
                """,
                (
                    row["run_id"],
                    row.get("user_id"),
                    row.get("status", "running"),
                    int(row.get("progress_pct") or 0),
                    row.get("current_stage"),
                    _json_or_none(row.get("request") or {}),
                    _json_or_none(row.get("rule_chain_snapshot") or {}),
                    _json_or_none(row.get("warnings") or []),
                    _json_or_none(row.get("progress_logs") or []),
                ),
            )

    def update_quant_backtest_progress(
        self,
        run_id: str,
        *,
        status: str | None = None,
        progress_pct: int | None = None,
        current_stage: str | None = None,
        log_message: str | None = None,
    ) -> None:
        current = self.get_quant_backtest_run(run_id) or {}
        logs = list(current.get("progress_logs") or [])
        if log_message:
            logs.append(_progress_log_entry(log_message))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE quant_backtest_runs
                SET status=COALESCE(%s, status),
                    progress_pct=COALESCE(%s, progress_pct),
                    current_stage=COALESCE(%s, current_stage),
                    progress_logs_json=%s
                WHERE run_id=%s
                """,
                (
                    status,
                    None if progress_pct is None else max(0, min(100, int(progress_pct))),
                    current_stage,
                    _json_or_none(logs),
                    run_id,
                ),
            )

    def finish_quant_backtest_run(self, run_id: str, metrics: dict, warnings: list[str], chart: dict | None = None) -> None:
        current = self.get_quant_backtest_run(run_id) or {}
        logs = list(current.get("progress_logs") or [])
        logs.append(_progress_log_entry("回测完成"))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE quant_backtest_runs
                SET status='completed',
                    progress_pct=100,
                    current_stage='回测完成',
                    metrics_json=%s,
                    chart_json=%s,
                    warnings_json=%s,
                    progress_logs_json=%s,
                    finished_at=%s
                WHERE run_id=%s
                """,
                (_json_or_none(metrics or {}), _json_or_none(chart or {}), _json_or_none(warnings or []), _json_or_none(logs), _utcnow(), run_id),
            )

    def fail_quant_backtest_run(self, run_id: str, error_message: str, warnings: list[str] | None = None) -> None:
        current = self.get_quant_backtest_run(run_id) or {}
        logs = list(current.get("progress_logs") or [])
        logs.append(_progress_log_entry(error_message))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE quant_backtest_runs
                SET status='failed',
                    progress_pct=100,
                    current_stage='执行失败',
                    warnings_json=%s,
                    progress_logs_json=%s,
                    error_message=%s,
                    finished_at=%s
                WHERE run_id=%s
                """,
                (_json_or_none(warnings or current.get("warnings") or []), _json_or_none(logs), error_message, _utcnow(), run_id),
            )

    def get_quant_backtest_run(self, run_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, user_id, status, progress_pct, current_stage,
                       request_json, rule_chain_snapshot_json, metrics_json, chart_json,
                       warnings_json, progress_logs_json, error_message, created_at, finished_at
                FROM quant_backtest_runs
                WHERE run_id=%s
                LIMIT 1
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "user_id": row[1],
            "status": row[2],
            "progress_pct": int(row[3] or 0),
            "current_stage": row[4] or "",
            "request": _decode_json_field(row[5], {}),
            "rule_chain_snapshot": _decode_json_field(row[6], {}),
            "metrics": _decode_json_field(row[7], {}),
            "chart": _decode_json_field(row[8], {}),
            "warnings": _decode_json_field(row[9], []),
            "progress_logs": _decode_json_field(row[10], []),
            "error_message": row[11],
            "created_at": str(row[12]) if row[12] else None,
            "finished_at": str(row[13]) if row[13] else None,
        }

    def init_option_lab_schema(self) -> None:
        schema_path = Path(__file__).parent / "sql" / "013_option_lab.sql"
        sql_text = schema_path.read_text(encoding="utf-8")
        statements = [stmt.strip() for stmt in sql_text.split(";") if stmt.strip()]
        with self.conn.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
            self._ensure_option_lab_schema_migrations(cursor)

    def _ensure_option_lab_schema_migrations(self, cursor) -> None:
        try:
            cursor.execute("SELECT macro_fields_json FROM option_strategy_candidates LIMIT 1")
        except Exception:
            cursor.execute(
                """
                ALTER TABLE option_strategy_candidates
                ADD COLUMN macro_fields_json JSON NULL COMMENT '宏观分析与综合评分展示字段'
                AFTER data_quality_json
                """
            )

    def create_option_evaluation_run(self, item: dict) -> None:
        item = _option_payload(item)
        sql = """
            INSERT INTO option_evaluation_runs
                (run_id, mode, user_id, market, code, risk_profile, request_json, status,
                 warnings_json, data_quality_json)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                mode=VALUES(mode),
                user_id=VALUES(user_id),
                market=VALUES(market),
                code=VALUES(code),
                risk_profile=VALUES(risk_profile),
                request_json=VALUES(request_json),
                status=VALUES(status),
                warnings_json=VALUES(warnings_json),
                data_quality_json=VALUES(data_quality_json)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                item.get("run_id"),
                item.get("mode") or "single",
                item.get("user_id"),
                item.get("market"),
                item.get("code"),
                item.get("risk_profile") or "balanced",
                _json_or_none(item.get("request") or item.get("request_json") or {}),
                item.get("status") or "running",
                _json_or_none(item.get("warnings") or []),
                _json_or_none(item.get("data_quality") or {}),
            ))

    def finish_option_evaluation_run(
        self,
        run_id: str,
        status: str = "completed",
        warnings: Optional[List[str]] = None,
        data_quality: Optional[dict] = None,
    ) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE option_evaluation_runs
                SET status=%s, warnings_json=%s, data_quality_json=%s, finished_at=%s
                WHERE run_id=%s
                """,
                (status, _json_or_none(warnings or []), _json_or_none(data_quality or {}), _utcnow(), run_id),
            )

    def fail_option_evaluation_run(
        self,
        run_id: str,
        error_message: str,
        warnings: Optional[List[str]] = None,
        data_quality: Optional[dict] = None,
    ) -> None:
        merged_warnings = list(warnings or [])
        if error_message:
            merged_warnings.append(error_message)
        self.finish_option_evaluation_run(run_id, "failed", merged_warnings, data_quality or {})

    def get_option_evaluation_run(self, run_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, mode, user_id, market, code, risk_profile, request_json, status,
                       warnings_json, data_quality_json, created_at, finished_at
                FROM option_evaluation_runs
                WHERE run_id=%s
                LIMIT 1
                """,
                (run_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "run_id": row[0],
            "mode": row[1],
            "user_id": row[2],
            "market": row[3],
            "code": row[4],
            "risk_profile": row[5],
            "request": _decode_json_field(row[6], {}),
            "status": row[7],
            "warnings": _decode_json_field(row[8], []),
            "data_quality": _decode_json_field(row[9], {}),
            "created_at": str(row[10]) if row[10] else None,
            "finished_at": str(row[11]) if row[11] else None,
        }

    def insert_option_evaluation_items(self, items: Iterable[dict]) -> None:
        values = []
        for raw_item in items:
            item = _option_payload(raw_item)
            best_strategy = item.get("best_strategy") or {}
            if item.get("child_run_id"):
                best_strategy = {**best_strategy, "child_run_id": item.get("child_run_id")}
            if item.get("candidates") is not None:
                best_strategy = {**best_strategy, "candidates": item.get("candidates") or []}
            values.append((
                item.get("run_id"),
                item.get("market"),
                item.get("code"),
                item.get("name"),
                item.get("status") or "running",
                _json_or_none(best_strategy),
                _json_or_none(item.get("data_quality") or {}),
                item.get("error_message"),
            ))
        if not values:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO option_evaluation_items
                    (run_id, market, code, name, status, best_strategy_json, data_quality_json, error_message)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                values,
            )

    def list_option_evaluation_items(self, run_id: str) -> List[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT run_id, market, code, name, status, best_strategy_json,
                       data_quality_json, error_message, created_at
                FROM option_evaluation_items
                WHERE run_id=%s
                ORDER BY id ASC
                """,
                (run_id,),
            )
            rows = cursor.fetchall() or []
        return [
            {
                "run_id": row[0],
                "market": row[1],
                "code": row[2],
                "name": row[3],
                "status": row[4],
                "best_strategy": _decode_json_field(row[5], {}),
                "data_quality": _decode_json_field(row[6], {}),
                "error_message": row[7],
                "created_at": str(row[8]) if row[8] else None,
            }
            for row in rows
        ]

    def insert_option_strategy_candidates(self, candidates: Iterable[dict]) -> None:
        values = []
        for raw_item in candidates:
            item = _option_payload(raw_item)
            values.append((
                item.get("candidate_id"),
                item.get("run_id"),
                item.get("market"),
                item.get("code"),
                item.get("strategy_key"),
                item.get("strategy_name") or item.get("策略名称"),
                item.get("score") if item.get("score") is not None else item.get("评分", 0),
                item.get("recommendation_status") or "observe",
                item.get("fit_reason") or item.get("适用理由"),
                _json_or_none(item.get("contract_details") or item.get("合约明细") or []),
                _json_or_none(item.get("risk_metrics") or {}),
                _json_or_none(item.get("order_suggestion") or {}),
                _json_or_none(item.get("warnings") or []),
                _json_or_none(item.get("data_quality") or item.get("数据质量") or {}),
                _json_or_none(_option_candidate_macro_fields(item)),
                item.get("snapshot_id"),
            ))
        if not values:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO option_strategy_candidates
                    (candidate_id, run_id, market, code, strategy_key, strategy_name, score,
                     recommendation_status, fit_reason, contract_details_json, risk_metrics_json,
                     order_suggestion_json, warnings_json, data_quality_json, macro_fields_json, snapshot_id)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    run_id=VALUES(run_id),
                    market=VALUES(market),
                    code=VALUES(code),
                    strategy_key=VALUES(strategy_key),
                    strategy_name=VALUES(strategy_name),
                    score=VALUES(score),
                    recommendation_status=VALUES(recommendation_status),
                    fit_reason=VALUES(fit_reason),
                    contract_details_json=VALUES(contract_details_json),
                    risk_metrics_json=VALUES(risk_metrics_json),
                    order_suggestion_json=VALUES(order_suggestion_json),
                    warnings_json=VALUES(warnings_json),
                    data_quality_json=VALUES(data_quality_json),
                    macro_fields_json=VALUES(macro_fields_json),
                    snapshot_id=VALUES(snapshot_id)
                """,
                values,
            )

    def get_option_strategy_candidate(self, candidate_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id, run_id, market, code, strategy_key, strategy_name, score,
                       recommendation_status, fit_reason, contract_details_json, risk_metrics_json,
                       order_suggestion_json, warnings_json, data_quality_json, macro_fields_json,
                       snapshot_id, created_at
                FROM option_strategy_candidates
                WHERE candidate_id=%s
                LIMIT 1
                """,
                (candidate_id,),
            )
            row = cursor.fetchone()
        return self._option_candidate_from_row(row) if row else None

    def list_option_strategy_candidates(self, run_id: str) -> List[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT candidate_id, run_id, market, code, strategy_key, strategy_name, score,
                       recommendation_status, fit_reason, contract_details_json, risk_metrics_json,
                       order_suggestion_json, warnings_json, data_quality_json, macro_fields_json,
                       snapshot_id, created_at
                FROM option_strategy_candidates
                WHERE run_id=%s
                ORDER BY score DESC, id ASC
                """,
                (run_id,),
            )
            rows = cursor.fetchall() or []
        return [self._option_candidate_from_row(row) for row in rows]

    def _option_candidate_from_row(self, row) -> dict:
        has_macro_fields = len(row) >= 17
        macro_fields = _decode_json_field(row[14], {}) if has_macro_fields else {}
        snapshot_index = 15 if has_macro_fields else 14
        created_index = 16 if has_macro_fields else 15
        payload = {
            "candidate_id": row[0],
            "run_id": row[1],
            "market": row[2],
            "code": row[3],
            "strategy_key": row[4],
            "strategy_name": row[5],
            "score": float(row[6] or 0),
            "recommendation_status": row[7],
            "fit_reason": row[8],
            "contract_details": _decode_json_field(row[9], []),
            "risk_metrics": _decode_json_field(row[10], {}),
            "order_suggestion": _decode_json_field(row[11], {}),
            "warnings": _decode_json_field(row[12], []),
            "data_quality": _decode_json_field(row[13], {}),
            "snapshot_id": row[snapshot_index],
            "created_at": str(row[created_index]) if row[created_index] else None,
        }
        if isinstance(macro_fields, dict):
            payload.update(macro_fields)
        payload.setdefault("期权评分", payload["score"])
        payload.setdefault("评分", payload["score"])
        return payload

    def create_option_order_plan(self, item: dict) -> None:
        item = _option_payload(item)
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO option_order_plans
                    (plan_id, candidate_id, user_id, status, contract_details_json,
                     order_suggestion_json, risk_metrics_json, warnings_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    candidate_id=VALUES(candidate_id),
                    user_id=VALUES(user_id),
                    status=VALUES(status),
                    contract_details_json=VALUES(contract_details_json),
                    order_suggestion_json=VALUES(order_suggestion_json),
                    risk_metrics_json=VALUES(risk_metrics_json),
                    warnings_json=VALUES(warnings_json)
                """,
                (
                    item.get("plan_id"),
                    item.get("candidate_id"),
                    item.get("user_id"),
                    item.get("status") or "planned",
                    _json_or_none(item.get("contract_details") or item.get("合约明细") or []),
                    _json_or_none(item.get("order_suggestion") or {}),
                    _json_or_none(item.get("risk_metrics") or {}),
                    _json_or_none(item.get("warnings") or []),
                ),
            )

    def get_option_order_plan(self, plan_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT plan_id, candidate_id, user_id, status, contract_details_json,
                       order_suggestion_json, risk_metrics_json, warnings_json,
                       created_at, updated_at
                FROM option_order_plans
                WHERE plan_id=%s
                LIMIT 1
                """,
                (plan_id,),
            )
            row = cursor.fetchone()
        return self._option_order_plan_from_row(row) if row else None

    def list_option_order_plans(self, user_id: Optional[int] = None, limit: int = 50) -> List[dict]:
        if user_id is None:
            sql = """
                SELECT plan_id, candidate_id, user_id, status, contract_details_json,
                       order_suggestion_json, risk_metrics_json, warnings_json,
                       created_at, updated_at
                FROM option_order_plans
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (int(limit),)
        else:
            sql = """
                SELECT plan_id, candidate_id, user_id, status, contract_details_json,
                       order_suggestion_json, risk_metrics_json, warnings_json,
                       created_at, updated_at
                FROM option_order_plans
                WHERE user_id=%s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (user_id, int(limit))
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [self._option_order_plan_from_row(row) for row in rows]

    def _option_order_plan_from_row(self, row) -> dict:
        return {
            "plan_id": row[0],
            "candidate_id": row[1],
            "user_id": row[2],
            "status": row[3],
            "contract_details": _decode_json_field(row[4], []),
            "order_suggestion": _decode_json_field(row[5], {}),
            "risk_metrics": _decode_json_field(row[6], {}),
            "warnings": _decode_json_field(row[7], []),
            "created_at": str(row[8]) if row[8] else None,
            "updated_at": str(row[9]) if row[9] else None,
        }

    def create_option_tracked_position(self, item: dict) -> None:
        item = _option_payload(item)
        current_state = item.get("current_state") or {}
        if item.get("order_suggestion"):
            current_state.setdefault("订单建议", item.get("order_suggestion"))
        if item.get("止损价") is not None:
            current_state.setdefault("止损价", item.get("止损价"))
        if item.get("止盈价") is not None:
            current_state.setdefault("止盈价", item.get("止盈价"))
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO option_tracked_positions
                    (position_id, plan_id, user_id, market, code, strategy_name,
                     contract_details_json, filled_price, quantity, filled_at, fee,
                     status, current_state_json, current_action, closed_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    plan_id=VALUES(plan_id),
                    user_id=VALUES(user_id),
                    market=VALUES(market),
                    code=VALUES(code),
                    strategy_name=VALUES(strategy_name),
                    contract_details_json=VALUES(contract_details_json),
                    filled_price=VALUES(filled_price),
                    quantity=VALUES(quantity),
                    filled_at=VALUES(filled_at),
                    fee=VALUES(fee),
                    status=VALUES(status),
                    current_state_json=VALUES(current_state_json),
                    current_action=VALUES(current_action),
                    closed_at=VALUES(closed_at)
                """,
                (
                    item.get("position_id"),
                    item.get("plan_id"),
                    item.get("user_id"),
                    item.get("market"),
                    item.get("code"),
                    item.get("strategy_name") or item.get("策略名称"),
                    _json_or_none(item.get("contract_details") or item.get("合约明细") or []),
                    item.get("filled_price"),
                    int(item.get("quantity") or 1),
                    item.get("filled_at"),
                    item.get("fee"),
                    item.get("status") or "active",
                    _json_or_none(current_state),
                    item.get("current_action"),
                    item.get("closed_at"),
                ),
            )

    def update_option_tracked_position_state(
        self,
        position_id: str,
        current_state: Optional[dict],
        current_action: Optional[str],
        status: Optional[str] = None,
        closed_at: Optional[datetime] = None,
    ) -> None:
        assignments = ["current_state_json=%s", "current_action=%s"]
        params: List[Any] = [_json_or_none(current_state or {}), current_action]
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
        if closed_at is not None:
            assignments.append("closed_at=%s")
            params.append(closed_at)
        params.append(position_id)
        with self.conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE option_tracked_positions SET {', '.join(assignments)} WHERE position_id=%s",
                tuple(params),
            )

    def get_option_tracked_position(self, position_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT position_id, plan_id, user_id, market, code, strategy_name,
                       contract_details_json, filled_price, quantity, filled_at, fee,
                       status, current_state_json, current_action, created_at, updated_at, closed_at
                FROM option_tracked_positions
                WHERE position_id=%s
                LIMIT 1
                """,
                (position_id,),
            )
            row = cursor.fetchone()
        return self._option_position_from_row(row) if row else None

    def list_option_tracked_positions(
        self,
        user_id: Optional[int] = None,
        status: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        where_parts = []
        params: List[Any] = []
        if user_id is not None:
            where_parts.append("user_id=%s")
            params.append(user_id)
        if status is not None:
            where_parts.append("status=%s")
            params.append(status)
        where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        params.append(int(limit))
        sql = f"""
            SELECT position_id, plan_id, user_id, market, code, strategy_name,
                   contract_details_json, filled_price, quantity, filled_at, fee,
                   status, current_state_json, current_action, created_at, updated_at, closed_at
            FROM option_tracked_positions
            {where_sql}
            ORDER BY updated_at DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, tuple(params))
            rows = cursor.fetchall() or []
        return [self._option_position_from_row(row) for row in rows]

    def _option_position_from_row(self, row) -> dict:
        current_state = _decode_json_field(row[12], {})
        return {
            "position_id": row[0],
            "plan_id": row[1],
            "user_id": row[2],
            "market": row[3],
            "code": row[4],
            "strategy_name": row[5],
            "contract_details": _decode_json_field(row[6], []),
            "filled_price": float(row[7]) if row[7] is not None else None,
            "quantity": int(row[8] or 0),
            "filled_at": str(row[9]) if row[9] else None,
            "fee": float(row[10]) if row[10] is not None else None,
            "status": row[11],
            "current_state": current_state,
            "current_action": row[13],
            "order_suggestion": current_state.get("订单建议") or {},
            "止损价": current_state.get("止损价"),
            "止盈价": current_state.get("止盈价"),
            "created_at": str(row[14]) if row[14] else None,
            "updated_at": str(row[15]) if row[15] else None,
            "closed_at": str(row[16]) if row[16] else None,
        }

    def insert_option_monitor_events(self, events: Iterable[dict]) -> None:
        values = []
        for raw_item in events:
            item = _option_payload(raw_item)
            severity = item.get("severity") or "info"
            if hasattr(severity, "value_key"):
                severity = severity.value_key
            values.append((
                item.get("event_id"),
                item.get("position_id"),
                severity,
                item.get("event_type"),
                item.get("message"),
                item.get("snapshot_id"),
                1 if item.get("pushed_feishu") else 0,
            ))
        if not values:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO option_monitor_events
                    (event_id, position_id, severity, event_type, message, snapshot_id, pushed_feishu)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    severity=VALUES(severity),
                    event_type=VALUES(event_type),
                    message=VALUES(message),
                    snapshot_id=VALUES(snapshot_id),
                    pushed_feishu=VALUES(pushed_feishu)
                """,
                values,
            )

    def list_option_monitor_events(
        self,
        position_id: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        if position_id:
            sql = """
                SELECT event_id, position_id, severity, event_type, message,
                       snapshot_id, pushed_feishu, created_at
                FROM option_monitor_events
                WHERE position_id=%s
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (position_id, int(limit))
        else:
            sql = """
                SELECT event_id, position_id, severity, event_type, message,
                       snapshot_id, pushed_feishu, created_at
                FROM option_monitor_events
                ORDER BY created_at DESC
                LIMIT %s
            """
            params = (int(limit),)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "event_id": row[0],
                "position_id": row[1],
                "severity": row[2],
                "event_type": row[3],
                "message": row[4],
                "snapshot_id": row[5],
                "pushed_feishu": bool(row[6]),
                "created_at": str(row[7]) if row[7] else None,
            }
            for row in rows
        ]

    def create_option_market_snapshot(self, item: dict) -> None:
        item = _option_payload(item)
        underlying = item.get("underlying") or {}
        market = item.get("market") or underlying.get("market")
        code = item.get("code") or underlying.get("code")
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO option_market_snapshots
                    (snapshot_id, provider, market, code, underlying_json, option_chain_json,
                     selected_quotes_json, data_quality_json, raw_payload_json, quote_time)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    provider=VALUES(provider),
                    market=VALUES(market),
                    code=VALUES(code),
                    underlying_json=VALUES(underlying_json),
                    option_chain_json=VALUES(option_chain_json),
                    selected_quotes_json=VALUES(selected_quotes_json),
                    data_quality_json=VALUES(data_quality_json),
                    raw_payload_json=VALUES(raw_payload_json),
                    quote_time=VALUES(quote_time)
                """,
                (
                    item.get("snapshot_id"),
                    item.get("provider"),
                    market,
                    code,
                    _json_or_none(underlying),
                    _json_or_none(item.get("option_chain") or item.get("option_quotes") or []),
                    _json_or_none(item.get("selected_quotes") or []),
                    _json_or_none(item.get("data_quality") or {}),
                    _json_or_none(item.get("raw_payload") or {}),
                    item.get("quote_time"),
                ),
            )

    def get_option_market_snapshot(self, snapshot_id: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT snapshot_id, provider, market, code, underlying_json, option_chain_json,
                       selected_quotes_json, data_quality_json, raw_payload_json, quote_time, created_at
                FROM option_market_snapshots
                WHERE snapshot_id=%s
                LIMIT 1
                """,
                (snapshot_id,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "snapshot_id": row[0],
            "provider": row[1],
            "market": row[2],
            "code": row[3],
            "underlying": _decode_json_field(row[4], {}),
            "option_chain": _decode_json_field(row[5], []),
            "selected_quotes": _decode_json_field(row[6], []),
            "data_quality": _decode_json_field(row[7], {}),
            "raw_payload": _decode_json_field(row[8], {}),
            "quote_time": str(row[9]) if row[9] else None,
            "created_at": str(row[10]) if row[10] else None,
        }

    def upsert_option_macro_analysis_cache(self, item: dict) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO option_macro_analysis_cache
                    (cache_key, market, code, analysis_profile, macro_score, macro_direction,
                     news_impact, hot_sector_mark, main_force_risk_level, summary,
                     positive_factors_json, risk_factors_json, macro_factors_json,
                     source_urls_json, warnings_json, provider, expires_at, raw_payload_json)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE
                    market=VALUES(market),
                    code=VALUES(code),
                    analysis_profile=VALUES(analysis_profile),
                    macro_score=VALUES(macro_score),
                    macro_direction=VALUES(macro_direction),
                    news_impact=VALUES(news_impact),
                    hot_sector_mark=VALUES(hot_sector_mark),
                    main_force_risk_level=VALUES(main_force_risk_level),
                    summary=VALUES(summary),
                    positive_factors_json=VALUES(positive_factors_json),
                    risk_factors_json=VALUES(risk_factors_json),
                    macro_factors_json=VALUES(macro_factors_json),
                    source_urls_json=VALUES(source_urls_json),
                    warnings_json=VALUES(warnings_json),
                    provider=VALUES(provider),
                    expires_at=VALUES(expires_at),
                    raw_payload_json=VALUES(raw_payload_json)
                """,
                (
                    item.get("cache_key"),
                    item.get("market"),
                    item.get("code"),
                    item.get("analysis_profile") or "default",
                    item.get("macro_score") if item.get("macro_score") is not None else item.get("宏观分析评分"),
                    item.get("macro_direction") or item.get("宏观方向"),
                    item.get("news_impact") or item.get("新闻影响"),
                    item.get("hot_sector_mark") or item.get("热点匹配"),
                    item.get("main_force_risk_level") or item.get("主力资金风险"),
                    item.get("summary") or item.get("宏观摘要"),
                    _json_or_none(item.get("positive_factors") or item.get("关键利好因素") or []),
                    _json_or_none(item.get("risk_factors") or item.get("关键风险因素") or []),
                    _json_or_none(item.get("macro_factors") or item.get("宏观/政策因素") or []),
                    _json_or_none(item.get("source_urls") or item.get("信息来源") or []),
                    _json_or_none(item.get("warnings") or []),
                    item.get("provider"),
                    _mysql_datetime_or_none(item.get("expires_at")),
                    _json_or_none(item.get("raw_payload") or item),
                ),
            )

    def save_option_macro_analysis_cache(self, cache_key: str, row: dict) -> None:
        self.upsert_option_macro_analysis_cache({**row, "cache_key": cache_key})

    def get_option_macro_analysis_cache(self, cache_key: str) -> Optional[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT cache_key, market, code, analysis_profile, macro_score, macro_direction,
                       news_impact, hot_sector_mark, main_force_risk_level, summary,
                       positive_factors_json, risk_factors_json, macro_factors_json,
                       source_urls_json, warnings_json, provider, created_at, expires_at,
                       raw_payload_json
                FROM option_macro_analysis_cache
                WHERE cache_key=%s
                LIMIT 1
                """,
                (cache_key,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "cache_key": row[0],
            "market": row[1],
            "code": row[2],
            "analysis_profile": row[3],
            "macro_score": float(row[4]) if row[4] is not None else None,
            "macro_direction": row[5],
            "news_impact": row[6],
            "hot_sector_mark": row[7],
            "main_force_risk_level": row[8],
            "summary": row[9],
            "positive_factors": _decode_json_field(row[10], []),
            "risk_factors": _decode_json_field(row[11], []),
            "macro_factors": _decode_json_field(row[12], []),
            "source_urls": _decode_json_field(row[13], []),
            "warnings": _decode_json_field(row[14], []),
            "provider": row[15],
            "created_at": str(row[16]) if row[16] else None,
            "expires_at": str(row[17]) if row[17] else None,
            "raw_payload": _decode_json_field(row[18], {}),
        }

    def save_run(self, run_id: str, item: dict) -> None:
        payload = _option_payload(item)
        payload.setdefault("run_id", run_id)
        self.create_option_evaluation_run(payload)

    def save_candidates(self, run_id: str, candidates: Iterable[Any]) -> None:
        payloads = []
        for candidate in candidates:
            payload = _option_payload(candidate)
            payload.setdefault("run_id", run_id)
            payloads.append(payload)
        self.insert_option_strategy_candidates(payloads)

    def get_candidate(self, candidate_id: str) -> Optional[dict]:
        return self.get_option_strategy_candidate(candidate_id)

    def save_order_plan(self, plan_or_candidate_id: Any, user_id: Optional[int] = None) -> dict:
        if isinstance(plan_or_candidate_id, dict) or hasattr(plan_or_candidate_id, "to_dict"):
            payload = _option_payload(plan_or_candidate_id)
        else:
            candidate = self.get_option_strategy_candidate(str(plan_or_candidate_id))
            if not candidate:
                raise ValueError(f"option candidate not found: {plan_or_candidate_id}")
            payload = {
                "plan_id": f"oplan_{secrets.token_hex(12)}",
                "candidate_id": candidate["candidate_id"],
                "user_id": user_id,
                "status": "planned",
                "contract_details": candidate.get("contract_details") or [],
                "order_suggestion": candidate.get("order_suggestion") or {},
                "risk_metrics": candidate.get("risk_metrics") or {},
                "warnings": candidate.get("warnings") or [],
            }
        self.create_option_order_plan(payload)
        return self.get_option_order_plan(payload["plan_id"]) or payload

    def save_position(self, position: dict) -> None:
        self.create_option_tracked_position(position)

    def get_position(self, position_id: str) -> Optional[dict]:
        return self.get_option_tracked_position(position_id)

    def save_events(self, position_id: str, events: Iterable[Any]) -> None:
        payloads = []
        for event in events:
            payload = _option_payload(event)
            payload.setdefault("position_id", position_id)
            payloads.append(payload)
        self.insert_option_monitor_events(payloads)

    # ------------------------------------------------------------------
    # Single stock runs
    # ------------------------------------------------------------------

    def create_single_stock_run(self, item: dict) -> None:
        sql = """
            INSERT INTO single_stock_runs
                (run_id, user_id, market, code, normalized_code, timeframe, chain_key, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                user_id=VALUES(user_id),
                market=VALUES(market),
                code=VALUES(code),
                normalized_code=VALUES(normalized_code),
                timeframe=VALUES(timeframe),
                chain_key=VALUES(chain_key),
                status=VALUES(status)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                item.get("run_id"),
                item.get("user_id"),
                item.get("market"),
                item.get("code"),
                item.get("normalized_code"),
                item.get("timeframe"),
                item.get("chain_key"),
                item.get("status") or "running",
            ))

    def finish_single_stock_run(
        self,
        run_id: str,
        passed: bool,
        status: str,
        data_source: Optional[str],
        warnings: Optional[List[str]] = None,
        ai_analysis: Optional[dict] = None,
    ) -> None:
        sql = """
            UPDATE single_stock_runs
            SET passed=%s, status=%s, data_source=%s, warnings_json=%s,
                ai_analysis_json=%s, finished_at=%s
            WHERE run_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                1 if passed else 0,
                status,
                data_source,
                _json_or_none(warnings or []),
                _json_or_none(ai_analysis),
                _utcnow(),
                run_id,
            ))

    def fail_single_stock_run(self, run_id: str, error_message: str, warnings: Optional[List[str]] = None) -> None:
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                UPDATE single_stock_runs
                SET status='failed', passed=0, warnings_json=%s, error_message=%s, finished_at=%s
                WHERE run_id=%s
                """,
                (_json_or_none(warnings or []), error_message, _utcnow(), run_id),
            )

    def get_single_stock_run(self, run_id: str) -> Optional[dict]:
        sql = """
            SELECT run_id, user_id, market, code, normalized_code, timeframe, passed,
                   chain_key, data_source, status, warnings_json, ai_analysis_json, result_json, agent_id,
                   claimed_at, heartbeat_at, error_message, created_at, finished_at
            FROM single_stock_runs
            WHERE run_id=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (run_id,))
            row = cursor.fetchone()
        if not row:
            return None
        result_json = _decode_json_field(row[12], {})
        item = {
            "job_type": "single_stock",
            "job_id": row[0],
            "run_id": row[0],
            "user_id": row[1],
            "market": row[2],
            "code": row[3],
            "normalized_code": row[4],
            "timeframe": row[5],
            "passed": bool(row[6]),
            "chain_key": row[7],
            "data_source": row[8],
            "status": row[9],
            "warnings": _decode_json_field(row[10], []),
            "ai_analysis": _decode_json_field(row[11], None),
            "result_json": result_json,
            "agent_id": row[13],
            "claimed_at": str(row[14]) if row[14] else None,
            "heartbeat_at": str(row[15]) if row[15] else None,
            "error_message": row[16],
            "created_at": str(row[17]) if row[17] else None,
            "finished_at": str(row[18]) if row[18] else None,
            "rule_details": self.get_single_stock_rule_details(row[0]),
        }
        if isinstance(result_json, dict):
            for key, value in result_json.items():
                item.setdefault(key, value)
        return item

    def list_single_stock_runs(self, limit: int = 50) -> List[dict]:
        sql = """
            SELECT run_id, user_id, market, code, normalized_code, timeframe, passed,
                   chain_key, data_source, status, warnings_json, ai_analysis_json, result_json,
                   agent_id, claimed_at, heartbeat_at, error_message, created_at, finished_at
            FROM single_stock_runs
            ORDER BY created_at DESC
            LIMIT %s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (int(limit),))
            rows = cursor.fetchall() or []
        result = []
        for row in rows:
            result_json = _decode_json_field(row[12], {})
            item = {
                "job_type": "single_stock",
                "job_id": row[0],
                "run_id": row[0],
                "user_id": row[1],
                "market": row[2],
                "code": row[3],
                "normalized_code": row[4],
                "timeframe": row[5],
                "passed": bool(row[6]),
                "chain_key": row[7],
                "data_source": row[8],
                "status": row[9],
                "warnings": _decode_json_field(row[10], []),
                "ai_analysis": _decode_json_field(row[11], None),
                "agent_id": row[13],
                "claimed_at": str(row[14]) if row[14] else None,
                "heartbeat_at": str(row[15]) if row[15] else None,
                "error_message": row[16],
                "created_at": str(row[17]) if row[17] else None,
                "finished_at": str(row[18]) if row[18] else None,
            }
            if isinstance(result_json, dict):
                item["name"] = result_json.get("name")
                item["sector"] = result_json.get("sector")
                item["industry"] = result_json.get("industry")
                item["chain_name"] = (result_json.get("rule_chain") or {}).get("chain_name")
            result.append(item)
        return result

    def complete_single_stock_run_from_agent(self, run_id: str, result: dict, rule_details: Iterable[dict]) -> None:
        sql = """
            UPDATE single_stock_runs
            SET passed=%s, status=%s, data_source=%s, warnings_json=%s,
                ai_analysis_json=%s, result_json=%s,
                chain_key=COALESCE(%s, chain_key),
                finished_at=%s
            WHERE run_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (
                1 if result.get("passed") else 0,
                result.get("status") or "completed",
                result.get("data_source"),
                _json_or_none(result.get("warnings") or []),
                _json_or_none(result.get("ai_analysis")),
                _json_or_none(result),
                ((result.get("rule_chain") or {}).get("chain_key") if isinstance(result.get("rule_chain"), dict) else None),
                _utcnow(),
                run_id,
            ))
        self.insert_single_stock_rule_details(run_id, rule_details)

    def insert_single_stock_rule_details(self, run_id: str, rows: Iterable[dict]) -> None:
        values = []
        for index, item in enumerate(rows):
            values.append((
                run_id,
                item.get("rule_key"),
                item.get("rule_name"),
                item.get("rule_type"),
                item.get("result"),
                item.get("reason"),
                _json_or_none(item.get("details")),
                int(item.get("display_order") if item.get("display_order") is not None else index),
            ))
        if not values:
            return
        with self.conn.cursor() as cursor:
            cursor.execute("DELETE FROM single_stock_rule_details WHERE run_id=%s", (run_id,))
            cursor.executemany(
                """
                INSERT INTO single_stock_rule_details
                    (run_id, rule_key, rule_name, rule_type, result, reason, details_json, display_order)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                values,
            )

    def get_single_stock_rule_details(self, run_id: str) -> List[dict]:
        sql = """
            SELECT rule_key, rule_name, rule_type, result, reason, details_json, display_order
            FROM single_stock_rule_details
            WHERE run_id=%s
            ORDER BY display_order ASC, id ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (run_id,))
            rows = cursor.fetchall() or []
        return [
            {
                "rule_key": row[0],
                "rule_name": row[1],
                "rule_type": row[2],
                "result": row[3],
                "reason": row[4],
                "details": _decode_json_field(row[5], {}),
                "display_order": int(row[6] or 0),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Screening Rule Engine
    # ------------------------------------------------------------------

    def init_rule_schema(self):
        """初始化数据库化规则引擎表，并写入默认市场规则。"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_rule_metadata (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
                    rule_key VARCHAR(64) NOT NULL COMMENT '原子规则键',
                    rule_name VARCHAR(128) NOT NULL COMMENT '规则展示名称',
                    rule_type VARCHAR(16) NOT NULL COMMENT 'filter/strategy',
                    strategy_category VARCHAR(16) NULL COMMENT 'technical/macro',
                    implementation VARCHAR(128) NOT NULL COMMENT '代码侧白名单实现名',
                    params_json JSON NULL COMMENT '规则参数',
                    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
                    display_order INT NOT NULL DEFAULT 100 COMMENT '执行展示顺序',
                    description VARCHAR(512) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_rule_metadata_market_key (market, rule_key),
                    KEY idx_rule_metadata_market_enabled (market, enabled),
                    KEY idx_rule_metadata_type (rule_type)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='筛选原子规则元数据'
                """
            )
            try:
                cursor.execute("SELECT `strategy_category` FROM screening_rule_metadata LIMIT 1")
            except Exception:
                try:
                    cursor.execute(
                        "ALTER TABLE screening_rule_metadata "
                        "ADD COLUMN strategy_category VARCHAR(16) NULL COMMENT 'technical/macro' AFTER rule_type"
                    )
                except Exception:
                    pass
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_rule_chains (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
                    timeframe VARCHAR(16) NOT NULL DEFAULT '*' COMMENT '适用周期，* 表示通用规则链',
                    chain_key VARCHAR(64) NOT NULL COMMENT '规则链键',
                    chain_name VARCHAR(128) NOT NULL COMMENT '规则链名称',
                    expression_json JSON NOT NULL COMMENT '规则链 JSON DSL',
                    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
                    priority INT NOT NULL DEFAULT 100 COMMENT '优先级，越小越优先',
                    description VARCHAR(512) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_rule_chains_market_timeframe_key (market, timeframe, chain_key),
                    KEY idx_rule_chains_market_timeframe_enabled (market, timeframe, enabled, priority)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='筛选规则使用链'
                """
            )
            self._ensure_rule_chain_timeframe_scope(cursor)
            self._ensure_rule_chain_indexes(cursor)
        self.seed_default_screening_rules()

    def _ensure_rule_chain_timeframe_scope(self, cursor):
        """兼容旧库：为规则链补齐 timeframe 维度和索引。"""
        try:
            cursor.execute("SELECT `timeframe` FROM screening_rule_chains LIMIT 1")
        except Exception:
            try:
                cursor.execute(
                    "ALTER TABLE screening_rule_chains "
                    "ADD COLUMN timeframe VARCHAR(16) NOT NULL DEFAULT '*' "
                    "COMMENT '适用周期，* 表示通用规则链' AFTER market"
                )
            except Exception:
                pass
        try:
            cursor.execute("ALTER TABLE screening_rule_chains DROP INDEX uk_rule_chains_market_key")
        except Exception:
            pass

    def _ensure_rule_chain_indexes(self, cursor):
        try:
            cursor.execute(
                "ALTER TABLE screening_rule_chains "
                "ADD UNIQUE KEY uk_rule_chains_market_timeframe_key (market, timeframe, chain_key)"
            )
        except Exception:
            pass
        try:
            cursor.execute("ALTER TABLE screening_rule_chains DROP INDEX idx_rule_chains_market_enabled")
        except Exception:
            pass
        try:
            cursor.execute(
                "ALTER TABLE screening_rule_chains "
                "ADD KEY idx_rule_chains_market_timeframe_enabled (market, timeframe, enabled, priority)"
            )
        except Exception:
            pass

    def seed_default_screening_rules(self):
        """写入默认规则配置；已有配置保持不变。"""
        metadata_rows = []
        for market in DEFAULT_RULE_MARKETS:
            for (
                rule_key,
                rule_name,
                rule_type,
                strategy_category,
                implementation,
                params,
                enabled,
                display_order,
                description,
            ) in DEFAULT_RULE_METADATA:
                metadata_rows.append((
                    market,
                    rule_key,
                    _default_rule_name_for_market(market, rule_key, rule_name),
                    rule_type,
                    strategy_category,
                    implementation,
                    json.dumps(
                        _default_rule_params_for_market(market, rule_key, params),
                        ensure_ascii=False,
                    ),
                    1 if enabled else 0,
                    display_order,
                    _default_rule_description_for_market(market, rule_key, description),
                ))

        chain_rows = []
        for market in DEFAULT_RULE_MARKETS:
            chain_rows.append((
                market,
                "*",
                DEFAULT_RULE_CHAIN_KEY,
                "左一战法与其他策略默认链",
                json.dumps(_default_rule_chain_expression_for_market(market), ensure_ascii=False),
                1,
                100,
                "启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中",
            ))
            chain_rows.append((
                market,
                "*",
                "trend_capital_accumulation_watch",
                "趋势主力缩量观察链",
                json.dumps(TREND_CAPITAL_ACCUMULATION_WATCH_EXPRESSION, ensure_ascii=False),
                0,
                300,
                "默认关闭的试跑链：基于现有上涨趋势/放量规则做观察，主力资金与热点板块原子规则接入后可扩展",
            ))

        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT IGNORE INTO screening_rule_metadata
                    (market, rule_key, rule_name, rule_type, strategy_category, implementation,
                     params_json, enabled, display_order, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                metadata_rows,
            )
            cursor.executemany(
                """
                INSERT IGNORE INTO screening_rule_chains
                    (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                chain_rows,
            )

    def get_screening_rule_metadata(self, market: str) -> List[dict]:
        """读取某个市场的所有原子规则元数据。"""
        sql = """
            SELECT market, rule_key, rule_name, rule_type, strategy_category, implementation,
                   params_json, enabled, display_order, description
            FROM screening_rule_metadata
            WHERE market=%s
            ORDER BY display_order, id
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market,))
            rows = cursor.fetchall() or []
        return [
            {
                "market": row[0],
                "rule_key": row[1],
                "rule_name": row[2],
                "rule_type": row[3],
                "strategy_category": row[4],
                "implementation": row[5],
                "params_json": _decode_json_field(row[6], {}),
                "enabled": bool(row[7]),
                "display_order": int(row[8] or 0),
                "description": row[9],
            }
            for row in rows
        ]

    def get_active_screening_rule_chain(self, market: str, timeframe: str = "*") -> Optional[dict]:
        """读取某个市场和周期优先级最高的启用规则链，精确周期优先。"""
        timeframe = str(timeframe or "*")
        sql = """
            SELECT market, timeframe, chain_key, chain_name, expression_json,
                   enabled, priority, description
            FROM screening_rule_chains
            WHERE market=%s AND enabled=1 AND timeframe IN (%s, '*')
            ORDER BY CASE WHEN timeframe=%s THEN 0 ELSE 1 END, priority ASC, id ASC
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, timeframe, timeframe))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "market": row[0],
            "timeframe": row[1],
            "chain_key": row[2],
            "chain_name": row[3],
            "expression_json": _decode_json_field(row[4], {}),
            "enabled": bool(row[5]),
            "priority": int(row[6] or 100),
            "description": row[7],
        }

    def list_screening_rule_chains(self, market: str, timeframe: Optional[str] = None) -> List[dict]:
        """读取某个市场的规则链，包含未启用的试跑链。"""
        if timeframe:
            timeframe = str(timeframe or "*")
            sql = """
                SELECT market, timeframe, chain_key, chain_name, expression_json,
                       enabled, priority, description
                FROM screening_rule_chains
                WHERE market=%s AND timeframe IN (%s, '*')
                ORDER BY CASE WHEN timeframe=%s THEN 0 ELSE 1 END, enabled DESC, priority ASC, id ASC
            """
            params = (market, timeframe, timeframe)
        else:
            sql = """
                SELECT market, timeframe, chain_key, chain_name, expression_json,
                       enabled, priority, description
                FROM screening_rule_chains
                WHERE market=%s
                ORDER BY timeframe ASC, enabled DESC, priority ASC, id ASC
            """
            params = (market,)
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "market": row[0],
                "timeframe": row[1],
                "chain_key": row[2],
                "chain_name": row[3],
                "expression_json": _decode_json_field(row[4], {}),
                "enabled": bool(row[5]),
                "priority": int(row[6] or 100),
                "description": row[7],
            }
            for row in rows
        ]

    def get_screening_rule_chain(self, market: str, chain_key: str, timeframe: str = "*") -> Optional[dict]:
        """按 key 读取某个市场和周期规则链；显式试跑允许读取未启用链。"""
        timeframe = str(timeframe or "*")
        sql = """
            SELECT market, timeframe, chain_key, chain_name, expression_json,
                   enabled, priority, description
            FROM screening_rule_chains
            WHERE market=%s AND chain_key=%s AND timeframe IN (%s, '*')
            ORDER BY CASE WHEN timeframe=%s THEN 0 ELSE 1 END, id ASC
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, chain_key, timeframe, timeframe))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "market": row[0],
            "timeframe": row[1],
            "chain_key": row[2],
            "chain_name": row[3],
            "expression_json": _decode_json_field(row[4], {}),
            "enabled": bool(row[5]),
            "priority": int(row[6] or 100),
            "description": row[7],
        }

    def create_screening_rule_chain(self, item: dict) -> None:
        sql = """
            INSERT INTO screening_rule_chains
                (market, timeframe, chain_key, chain_name, expression_json, enabled, priority, description)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    item.get("market"),
                    item.get("timeframe") or "*",
                    item.get("chain_key"),
                    item.get("chain_name"),
                    _json_or_none(item.get("expression_json")) or "{}",
                    1 if item.get("enabled") else 0,
                    int(item.get("priority") or 100),
                    item.get("description"),
                ),
            )

    def update_screening_rule_chain(self, market: str, timeframe: str, chain_key: str, item: dict) -> bool:
        sql = """
            UPDATE screening_rule_chains
            SET chain_name=%s,
                expression_json=%s,
                enabled=%s,
                priority=%s,
                description=%s
            WHERE market=%s AND timeframe=%s AND chain_key=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    item.get("chain_name"),
                    _json_or_none(item.get("expression_json")) or "{}",
                    1 if item.get("enabled") else 0,
                    int(item.get("priority") or 100),
                    item.get("description"),
                    market,
                    timeframe,
                    chain_key,
                ),
            )
            return cursor.rowcount > 0

    def delete_screening_rule_chain(self, market: str, timeframe: str, chain_key: str) -> bool:
        sql = """
            DELETE FROM screening_rule_chains
            WHERE market=%s AND timeframe=%s AND chain_key=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, timeframe, chain_key))
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # Signal analysis
    # ------------------------------------------------------------------

    def init_sector_schema(self):
        """初始化股票板块成分关系表。"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_sector_memberships (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    sector_type VARCHAR(32) NOT NULL,
                    sector_code VARCHAR(64) NULL,
                    sector_name VARCHAR(128) NOT NULL,
                    source VARCHAR(32) NOT NULL,
                    as_of_date DATE NOT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                        ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_sector_member (market, code, sector_type, sector_name, source),
                    KEY idx_sector_member_code (market, code),
                    KEY idx_sector_member_sector (market, sector_type, sector_name)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

    def upsert_stock_sector_memberships(self, rows: Iterable[dict]):
        """插入或更新股票-板块成分关系。"""
        values = []
        for item in rows:
            market = str(item.get("market") or "").strip()
            code = str(item.get("code") or "").strip()
            sector_name = str(item.get("sector_name") or "").strip()
            if not (market and code and sector_name):
                continue
            values.append((
                market,
                code,
                str(item.get("sector_type") or "sector").strip(),
                item.get("sector_code"),
                sector_name,
                str(item.get("source") or "unknown").strip(),
                item.get("as_of_date") or date.today(),
            ))
        if not values:
            return
        sql = """
            INSERT INTO stock_sector_memberships
                (market, code, sector_type, sector_code, sector_name, source, as_of_date)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                sector_code=VALUES(sector_code),
                as_of_date=VALUES(as_of_date)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, values)

    def get_sector_memberships_by_codes(self, market: str, codes: List[str]) -> Dict[str, List[dict]]:
        """按股票代码查询板块成分关系。"""
        codes = [str(code).strip() for code in codes if str(code).strip()]
        if not codes:
            return {}
        placeholders = ",".join(["%s"] * len(codes))
        sql = f"""
            SELECT code, sector_type, sector_code, sector_name, source, as_of_date
            FROM stock_sector_memberships
            WHERE market=%s AND code IN ({placeholders})
            ORDER BY
                CASE sector_type WHEN 'industry' THEN 0 WHEN 'sector' THEN 1 ELSE 2 END,
                as_of_date DESC,
                id ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, [market] + codes)
            rows = cursor.fetchall() or []
        result: Dict[str, List[dict]] = {}
        for row in rows:
            result.setdefault(row[0], []).append({
                "code": row[0],
                "sector_type": row[1],
                "sector_code": row[2],
                "sector_name": row[3],
                "source": row[4],
                "as_of_date": str(row[5]) if row[5] else None,
            })
        return result

    def init_signal_analysis_schema(self):
        """初始化选股信号 AI 辅助分析表。"""
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_signal_analysis (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    task_id VARCHAR(36) NOT NULL COMMENT '筛选任务 ID',
                    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
                    code VARCHAR(32) NOT NULL COMMENT '股票代码',
                    name VARCHAR(255) NULL COMMENT '股票名称',
                    check_date DATE NOT NULL COMMENT '筛选日期',
                    csv_path VARCHAR(1024) NULL COMMENT '来源 CSV 路径',
                    analysis_status VARCHAR(32) NOT NULL DEFAULT 'success' COMMENT 'success/error/skipped',
                    reliability_score DECIMAL(6,2) NULL COMMENT '信号可靠性评分 0-100',
                    confidence_score DECIMAL(6,2) NULL COMMENT '模型置信度 0-100',
                    signal_bias VARCHAR(32) NULL COMMENT 'bullish/bearish/neutral/avoid/unknown',
                    summary TEXT NULL COMMENT '模型摘要',
                    positive_factors JSON NULL COMMENT '利好因素',
                    risk_factors JSON NULL COMMENT '风险因素',
                    macro_factors JSON NULL COMMENT '宏观/政策因素',
                    company_events JSON NULL COMMENT '公司事件',
                    market_hot_news JSON NULL COMMENT '市场热点新闻',
                    company_hot_news JSON NULL COMMENT '公司热点新闻',
                    news_impact VARCHAR(64) NULL COMMENT '新闻影响判断',
                    news_sources JSON NULL COMMENT '新闻来源',
                    hot_sectors JSON NULL COMMENT '识别到的热点板块',
                    hot_sector_mark VARCHAR(32) NULL COMMENT '重点/相关/观察/无明确关联/未知',
                    matched_hot_sectors JSON NULL COMMENT '匹配到的热点板块',
                    hot_sector_relevance VARCHAR(64) NULL COMMENT '热点板块关联度',
                    hot_sector_reason TEXT NULL COMMENT '热点板块匹配理由',
                    hot_sector_sources JSON NULL COMMENT '热点板块来源',
                    source_urls JSON NULL COMMENT '信息来源 URL',
                    data_gaps JSON NULL COMMENT '数据缺失原因',
                    evidence_links JSON NULL COMMENT '引用来源明细',
                    factor_citations JSON NULL COMMENT '因素到引用来源的映射',
                    model VARCHAR(128) NULL COMMENT '模型名',
                    raw_response JSON NULL COMMENT '模型原始结构化响应',
                    error_message TEXT NULL COMMENT '错误信息',
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_signal_analysis_task_market_code (task_id, market, code),
                    KEY idx_signal_analysis_market_date (market, check_date),
                    KEY idx_signal_analysis_score (reliability_score),
                    KEY idx_signal_analysis_status (analysis_status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='选股结果搜索与模型辅助分析'
                """
            )
            signal_analysis_alters = [
                ("market_hot_news", "ALTER TABLE screening_signal_analysis ADD COLUMN market_hot_news JSON NULL COMMENT '市场热点新闻' AFTER company_events"),
                ("company_hot_news", "ALTER TABLE screening_signal_analysis ADD COLUMN company_hot_news JSON NULL COMMENT '公司热点新闻' AFTER market_hot_news"),
                ("news_impact", "ALTER TABLE screening_signal_analysis ADD COLUMN news_impact VARCHAR(64) NULL COMMENT '新闻影响判断' AFTER company_hot_news"),
                ("news_sources", "ALTER TABLE screening_signal_analysis ADD COLUMN news_sources JSON NULL COMMENT '新闻来源' AFTER news_impact"),
                ("hot_sectors", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sectors JSON NULL COMMENT '识别到的热点板块' AFTER company_events"),
                ("hot_sector_mark", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_mark VARCHAR(32) NULL COMMENT '重点/相关/观察/无明确关联/未知' AFTER hot_sectors"),
                ("matched_hot_sectors", "ALTER TABLE screening_signal_analysis ADD COLUMN matched_hot_sectors JSON NULL COMMENT '匹配到的热点板块' AFTER hot_sector_mark"),
                ("hot_sector_relevance", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_relevance VARCHAR(64) NULL COMMENT '热点板块关联度' AFTER matched_hot_sectors"),
                ("hot_sector_reason", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_reason TEXT NULL COMMENT '热点板块匹配理由' AFTER hot_sector_relevance"),
                ("hot_sector_sources", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_sources JSON NULL COMMENT '热点板块来源' AFTER hot_sector_reason"),
                ("data_gaps", "ALTER TABLE screening_signal_analysis ADD COLUMN data_gaps JSON NULL COMMENT '数据缺失原因' AFTER source_urls"),
                ("evidence_links", "ALTER TABLE screening_signal_analysis ADD COLUMN evidence_links JSON NULL COMMENT '引用来源明细' AFTER data_gaps"),
                ("factor_citations", "ALTER TABLE screening_signal_analysis ADD COLUMN factor_citations JSON NULL COMMENT '因素到引用来源的映射' AFTER evidence_links"),
            ]
            for column, alter_sql in signal_analysis_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM screening_signal_analysis LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass
        self.init_signal_analysis_cache_schema()

    def init_signal_analysis_cache_schema(self):
        with self.conn.cursor() as cursor:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS signal_analysis_cache (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    cache_key VARCHAR(200) NOT NULL,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    timeframe VARCHAR(16) NOT NULL,
                    analysis_profile VARCHAR(32) NOT NULL DEFAULT 'default',
                    trade_date DATE NOT NULL,
                    name VARCHAR(255) NULL,
                    analysis_status VARCHAR(32) NOT NULL DEFAULT 'success',
                    reliability_score DECIMAL(6,2) NULL,
                    confidence_score DECIMAL(6,2) NULL,
                    signal_bias VARCHAR(32) NULL,
                    summary TEXT NULL,
                    positive_factors JSON NULL,
                    risk_factors JSON NULL,
                    macro_factors JSON NULL,
                    company_events JSON NULL,
                    market_hot_news JSON NULL,
                    company_hot_news JSON NULL,
                    news_impact VARCHAR(64) NULL,
                    news_sources JSON NULL,
                    hot_sectors JSON NULL,
                    hot_sector_mark VARCHAR(32) NULL,
                    matched_hot_sectors JSON NULL,
                    hot_sector_relevance VARCHAR(64) NULL,
                    hot_sector_reason TEXT NULL,
                    hot_sector_sources JSON NULL,
                    source_urls JSON NULL,
                    data_gaps JSON NULL,
                    evidence_links JSON NULL,
                    factor_citations JSON NULL,
                    model VARCHAR(128) NULL,
                    raw_response JSON NULL,
                    error_message TEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_signal_analysis_cache_key (cache_key),
                    KEY idx_signal_analysis_cache_scope (market, code, timeframe, analysis_profile, trade_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='股票维度共享 signal analysis 缓存'
                """
            )

    def upsert_signal_analysis_results(self, results: Iterable[dict]):
        """写入或更新选股信号 AI 辅助分析结果。"""
        rows = []
        for item in results:
            code = str(item.get("code") or "").strip()
            task_id = str(item.get("task_id") or "").strip()
            market = str(item.get("market") or "").strip()
            if not (task_id and market and code):
                continue
            rows.append((
                task_id,
                market,
                code,
                item.get("name"),
                item.get("check_date"),
                item.get("csv_path"),
                item.get("analysis_status") or "success",
                item.get("reliability_score"),
                item.get("confidence_score"),
                item.get("signal_bias"),
                item.get("summary"),
                _json_or_none(item.get("positive_factors")),
                _json_or_none(item.get("risk_factors")),
                _json_or_none(item.get("macro_factors")),
                _json_or_none(item.get("company_events")),
                _json_or_none(item.get("market_hot_news")),
                _json_or_none(item.get("company_hot_news")),
                item.get("news_impact"),
                _json_or_none(item.get("news_sources")),
                _json_or_none(item.get("hot_sectors")),
                item.get("hot_sector_mark"),
                _json_or_none(item.get("matched_hot_sectors")),
                item.get("hot_sector_relevance"),
                item.get("hot_sector_reason"),
                _json_or_none(item.get("hot_sector_sources")),
                _json_or_none(item.get("source_urls")),
                _json_or_none(item.get("data_gaps")),
                _json_or_none(item.get("evidence_links")),
                _json_or_none(item.get("factor_citations")),
                item.get("model"),
                _json_or_none(item.get("raw_response")),
                item.get("error_message"),
            ))
        if not rows:
            return

        sql = """
            INSERT INTO screening_signal_analysis
                (task_id, market, code, name, check_date, csv_path, analysis_status,
                 reliability_score, confidence_score, signal_bias, summary,
                 positive_factors, risk_factors, macro_factors, company_events,
                 market_hot_news, company_hot_news, news_impact, news_sources,
                 hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                 hot_sector_reason, hot_sector_sources, source_urls, data_gaps,
                 evidence_links, factor_citations, model, raw_response,
                 error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                check_date=VALUES(check_date),
                csv_path=VALUES(csv_path),
                analysis_status=VALUES(analysis_status),
                reliability_score=VALUES(reliability_score),
                confidence_score=VALUES(confidence_score),
                signal_bias=VALUES(signal_bias),
                summary=VALUES(summary),
                positive_factors=VALUES(positive_factors),
                risk_factors=VALUES(risk_factors),
                macro_factors=VALUES(macro_factors),
                company_events=VALUES(company_events),
                market_hot_news=VALUES(market_hot_news),
                company_hot_news=VALUES(company_hot_news),
                news_impact=VALUES(news_impact),
                news_sources=VALUES(news_sources),
                hot_sectors=VALUES(hot_sectors),
                hot_sector_mark=VALUES(hot_sector_mark),
                matched_hot_sectors=VALUES(matched_hot_sectors),
                hot_sector_relevance=VALUES(hot_sector_relevance),
                hot_sector_reason=VALUES(hot_sector_reason),
                hot_sector_sources=VALUES(hot_sector_sources),
                source_urls=VALUES(source_urls),
                data_gaps=VALUES(data_gaps),
                evidence_links=VALUES(evidence_links),
                factor_citations=VALUES(factor_citations),
                model=VALUES(model),
                raw_response=VALUES(raw_response),
                error_message=VALUES(error_message)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_signal_analysis_results_by_task(self, task_id: str) -> List[dict]:
        sql = """
            SELECT task_id, market, code, name, check_date, csv_path, analysis_status,
                   reliability_score, confidence_score, signal_bias, summary,
                   positive_factors, risk_factors, macro_factors, company_events,
                   market_hot_news, company_hot_news, news_impact, news_sources,
                   hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                   hot_sector_reason, hot_sector_sources, source_urls, data_gaps,
                   evidence_links, factor_citations, model, raw_response,
                   error_message
            FROM screening_signal_analysis
            WHERE task_id=%s
            ORDER BY code ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            rows = cursor.fetchall() or []
        return [
            {
                "task_id": row[0],
                "market": row[1],
                "code": row[2],
                "name": row[3],
                "check_date": str(row[4]) if row[4] else None,
                "csv_path": row[5],
                "analysis_status": row[6],
                "reliability_score": float(row[7]) if row[7] is not None else None,
                "confidence_score": float(row[8]) if row[8] is not None else None,
                "signal_bias": row[9],
                "summary": row[10],
                "positive_factors": _decode_json_field(row[11], []),
                "risk_factors": _decode_json_field(row[12], []),
                "macro_factors": _decode_json_field(row[13], []),
                "company_events": _decode_json_field(row[14], []),
                "market_hot_news": _decode_json_field(row[15], []),
                "company_hot_news": _decode_json_field(row[16], []),
                "news_impact": row[17],
                "news_sources": _decode_json_field(row[18], []),
                "hot_sectors": _decode_json_field(row[19], []),
                "hot_sector_mark": row[20],
                "matched_hot_sectors": _decode_json_field(row[21], []),
                "hot_sector_relevance": row[22],
                "hot_sector_reason": row[23],
                "hot_sector_sources": _decode_json_field(row[24], []),
                "source_urls": _decode_json_field(row[25], []),
                "data_gaps": _decode_json_field(row[26], []),
                "evidence_links": _decode_json_field(row[27], []),
                "factor_citations": _decode_json_field(row[28], {}),
                "model": row[29],
                "raw_response": _decode_json_field(row[30], None),
                "error_message": row[31],
            }
            for row in rows
        ]

    def upsert_signal_analysis_cache(self, results: Iterable[dict]) -> None:
        rows = []
        for item in results:
            market = str(item.get("market") or "").strip()
            code = str(item.get("code") or "").strip()
            timeframe = str(item.get("timeframe") or "1d").strip() or "1d"
            analysis_profile = str(item.get("analysis_profile") or "default").strip() or "default"
            trade_date = item.get("check_date")
            if not (market and code and trade_date):
                continue
            cache_key = f"{market}:{code}:{timeframe}:{analysis_profile}:{trade_date}"
            rows.append((
                cache_key,
                market,
                code,
                timeframe,
                analysis_profile,
                trade_date,
                item.get("name"),
                item.get("analysis_status") or "success",
                item.get("reliability_score"),
                item.get("confidence_score"),
                item.get("signal_bias"),
                item.get("summary"),
                _json_or_none(item.get("positive_factors")),
                _json_or_none(item.get("risk_factors")),
                _json_or_none(item.get("macro_factors")),
                _json_or_none(item.get("company_events")),
                _json_or_none(item.get("market_hot_news")),
                _json_or_none(item.get("company_hot_news")),
                item.get("news_impact"),
                _json_or_none(item.get("news_sources")),
                _json_or_none(item.get("hot_sectors")),
                item.get("hot_sector_mark"),
                _json_or_none(item.get("matched_hot_sectors")),
                item.get("hot_sector_relevance"),
                item.get("hot_sector_reason"),
                _json_or_none(item.get("hot_sector_sources")),
                _json_or_none(item.get("source_urls")),
                _json_or_none(item.get("data_gaps")),
                _json_or_none(item.get("evidence_links")),
                _json_or_none(item.get("factor_citations")),
                item.get("model"),
                _json_or_none(item.get("raw_response")),
                item.get("error_message"),
            ))
        if not rows:
            return
        sql = """
            INSERT INTO signal_analysis_cache
                (cache_key, market, code, timeframe, analysis_profile, trade_date, name,
                 analysis_status, reliability_score, confidence_score, signal_bias, summary,
                 positive_factors, risk_factors, macro_factors, company_events,
                 market_hot_news, company_hot_news, news_impact, news_sources,
                 hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                 hot_sector_reason, hot_sector_sources, source_urls, data_gaps,
                 evidence_links, factor_citations, model, raw_response, error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                analysis_status=VALUES(analysis_status),
                reliability_score=VALUES(reliability_score),
                confidence_score=VALUES(confidence_score),
                signal_bias=VALUES(signal_bias),
                summary=VALUES(summary),
                positive_factors=VALUES(positive_factors),
                risk_factors=VALUES(risk_factors),
                macro_factors=VALUES(macro_factors),
                company_events=VALUES(company_events),
                market_hot_news=VALUES(market_hot_news),
                company_hot_news=VALUES(company_hot_news),
                news_impact=VALUES(news_impact),
                news_sources=VALUES(news_sources),
                hot_sectors=VALUES(hot_sectors),
                hot_sector_mark=VALUES(hot_sector_mark),
                matched_hot_sectors=VALUES(matched_hot_sectors),
                hot_sector_relevance=VALUES(hot_sector_relevance),
                hot_sector_reason=VALUES(hot_sector_reason),
                hot_sector_sources=VALUES(hot_sector_sources),
                source_urls=VALUES(source_urls),
                data_gaps=VALUES(data_gaps),
                evidence_links=VALUES(evidence_links),
                factor_citations=VALUES(factor_citations),
                model=VALUES(model),
                raw_response=VALUES(raw_response),
                error_message=VALUES(error_message)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_signal_analysis_cache(
        self,
        market: str,
        code: str,
        timeframe: str,
        analysis_profile: str,
        trade_date: date,
    ) -> Optional[dict]:
        cache_key = f"{market}:{code}:{timeframe}:{analysis_profile}:{trade_date}"
        sql = """
            SELECT market, code, timeframe, analysis_profile, trade_date, name, analysis_status,
                   reliability_score, confidence_score, signal_bias, summary,
                   positive_factors, risk_factors, macro_factors, company_events,
                   market_hot_news, company_hot_news, news_impact, news_sources,
                   hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                   hot_sector_reason, hot_sector_sources, source_urls, data_gaps,
                   evidence_links, factor_citations, model, raw_response, error_message
            FROM signal_analysis_cache
            WHERE cache_key=%s
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (cache_key,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "market": row[0],
            "code": row[1],
            "timeframe": row[2],
            "analysis_profile": row[3],
            "check_date": str(row[4]) if row[4] else None,
            "name": row[5],
            "analysis_status": row[6],
            "reliability_score": float(row[7]) if row[7] is not None else None,
            "confidence_score": float(row[8]) if row[8] is not None else None,
            "signal_bias": row[9],
            "summary": row[10],
            "positive_factors": _decode_json_field(row[11], []),
            "risk_factors": _decode_json_field(row[12], []),
            "macro_factors": _decode_json_field(row[13], []),
            "company_events": _decode_json_field(row[14], []),
            "market_hot_news": _decode_json_field(row[15], []),
            "company_hot_news": _decode_json_field(row[16], []),
            "news_impact": row[17],
            "news_sources": _decode_json_field(row[18], []),
            "hot_sectors": _decode_json_field(row[19], []),
            "hot_sector_mark": row[20],
            "matched_hot_sectors": _decode_json_field(row[21], []),
            "hot_sector_relevance": row[22],
            "hot_sector_reason": row[23],
            "hot_sector_sources": _decode_json_field(row[24], []),
            "source_urls": _decode_json_field(row[25], []),
            "data_gaps": _decode_json_field(row[26], []),
            "evidence_links": _decode_json_field(row[27], []),
            "factor_citations": _decode_json_field(row[28], {}),
            "model": row[29],
            "raw_response": _decode_json_field(row[30], None),
            "error_message": row[31],
        }

    # ------------------------------------------------------------------
    # Main-force risk analysis
    # ------------------------------------------------------------------

    def init_main_force_risk_schema(self):
        """初始化主力流出风险分析表和筛选结果摘要列。"""
        with self.conn.cursor() as cursor:
            result_alters = [
                (
                    "main_force_risk_level",
                    "ALTER TABLE screening_results ADD COLUMN main_force_risk_level VARCHAR(16) NULL COMMENT 'low/medium/high/unknown' AFTER close_price",
                ),
                (
                    "main_force_risk_score",
                    "ALTER TABLE screening_results ADD COLUMN main_force_risk_score DECIMAL(6,2) NULL COMMENT '主力流出风险分 0-100，越高风险越大' AFTER main_force_risk_level",
                ),
                (
                    "main_force_risk_summary",
                    "ALTER TABLE screening_results ADD COLUMN main_force_risk_summary VARCHAR(512) NULL COMMENT '主力流出风险摘要' AFTER main_force_risk_score",
                ),
                (
                    "main_force_risk_signals",
                    "ALTER TABLE screening_results ADD COLUMN main_force_risk_signals JSON NULL COMMENT '触发的主要风险信号摘要' AFTER main_force_risk_summary",
                ),
                (
                    "main_force_data_status",
                    "ALTER TABLE screening_results ADD COLUMN main_force_data_status JSON NULL COMMENT '资金/盘口/龙虎榜/筹码数据状态' AFTER main_force_risk_signals",
                ),
                (
                    "main_force_risk_updated_at",
                    "ALTER TABLE screening_results ADD COLUMN main_force_risk_updated_at DATETIME(6) NULL COMMENT '主力流出风险更新时间' AFTER main_force_data_status",
                ),
            ]
            for column, alter_sql in result_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM screening_results LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass
            try:
                cursor.execute("SHOW INDEX FROM screening_results WHERE Key_name='idx_screening_main_force_risk'")
                if not cursor.fetchall():
                    cursor.execute(
                        "ALTER TABLE screening_results ADD KEY idx_screening_main_force_risk "
                        "(main_force_risk_level, main_force_risk_score)"
                    )
            except Exception:
                pass

            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_main_force_risks (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    task_id VARCHAR(36) NOT NULL,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    name VARCHAR(255) NULL,
                    check_date DATE NOT NULL,
                    csv_path VARCHAR(1024) NULL,
                    analysis_status VARCHAR(32) NOT NULL DEFAULT 'success',
                    risk_level VARCHAR(16) NOT NULL DEFAULT 'unknown',
                    risk_score DECIMAL(6,2) NULL,
                    risk_summary TEXT NULL,
                    triggered_signals JSON NULL COMMENT '触发信号列表',
                    missing_data JSON NULL COMMENT '缺失/不适用/无权限数据项',
                    provider_status JSON NULL COMMENT '各数据源状态',
                    metrics_json JSON NULL COMMENT '归一化后的关键指标，不存完整盘口大对象',
                    error_message TEXT NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_main_force_task_market_code (task_id, market, code),
                    KEY idx_main_force_market_date (market, check_date),
                    KEY idx_main_force_risk (risk_level, risk_score),
                    KEY idx_main_force_status (analysis_status)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='主力流出风险分析明细'
                """
            )

    def upsert_main_force_risk_results(self, results: Iterable[dict]):
        """写入或更新主力流出风险分析明细。"""
        rows = []
        for item in results:
            code = str(item.get("code") or "").strip()
            task_id = str(item.get("task_id") or "").strip()
            market = str(item.get("market") or "").strip()
            if not (task_id and market and code):
                continue
            rows.append((
                task_id,
                market,
                code,
                item.get("name"),
                item.get("check_date"),
                item.get("csv_path"),
                item.get("analysis_status") or "success",
                item.get("risk_level") or "unknown",
                item.get("risk_score"),
                item.get("risk_summary"),
                _json_or_none(item.get("triggered_signals")),
                _json_or_none(item.get("missing_data")),
                _json_or_none(item.get("provider_status")),
                _json_or_none(item.get("metrics_json")),
                item.get("error_message"),
            ))
        if not rows:
            return
        sql = """
            INSERT INTO screening_main_force_risks
                (task_id, market, code, name, check_date, csv_path, analysis_status,
                 risk_level, risk_score, risk_summary, triggered_signals, missing_data,
                 provider_status, metrics_json, error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                check_date=VALUES(check_date),
                csv_path=VALUES(csv_path),
                analysis_status=VALUES(analysis_status),
                risk_level=VALUES(risk_level),
                risk_score=VALUES(risk_score),
                risk_summary=VALUES(risk_summary),
                triggered_signals=VALUES(triggered_signals),
                missing_data=VALUES(missing_data),
                provider_status=VALUES(provider_status),
                metrics_json=VALUES(metrics_json),
                error_message=VALUES(error_message)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_main_force_risk_results_by_task(self, task_id: str) -> List[dict]:
        sql = """
            SELECT task_id, market, code, name, check_date, csv_path, analysis_status,
                   risk_level, risk_score, risk_summary, triggered_signals, missing_data,
                   provider_status, metrics_json, error_message
            FROM screening_main_force_risks
            WHERE task_id=%s
            ORDER BY code ASC
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
            rows = cursor.fetchall() or []
        return [
            {
                "task_id": row[0],
                "market": row[1],
                "code": row[2],
                "name": row[3],
                "check_date": str(row[4]) if row[4] else None,
                "csv_path": row[5],
                "analysis_status": row[6],
                "risk_level": row[7],
                "risk_score": float(row[8]) if row[8] is not None else None,
                "risk_summary": row[9],
                "triggered_signals": _decode_json_field(row[10], []),
                "missing_data": _decode_json_field(row[11], []),
                "provider_status": _decode_json_field(row[12], {}),
                "metrics_json": _decode_json_field(row[13], {}),
                "error_message": row[14],
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # 迁移（保留兼容）
    # ------------------------------------------------------------------

    def migrate_add_sector_columns(self):
        """幂等迁移：为 stocks 表补充板块列（如果旧库缺少）"""
        alter_statements = [
            ("stocks", "sector", "ALTER TABLE stocks ADD COLUMN sector VARCHAR(128) NULL AFTER delisting_date"),
            ("stocks", "sector_code", "ALTER TABLE stocks ADD COLUMN sector_code VARCHAR(64) NULL AFTER sector"),
            ("stocks", "industry", "ALTER TABLE stocks ADD COLUMN industry VARCHAR(128) NULL AFTER sector_code"),
            ("stocks", "industry_code", "ALTER TABLE stocks ADD COLUMN industry_code VARCHAR(64) NULL AFTER industry"),
            ("stocks", "market_cap", "ALTER TABLE stocks ADD COLUMN market_cap DECIMAL(28,2) NULL AFTER industry_code"),
            ("stocks", "pe_ratio", "ALTER TABLE stocks ADD COLUMN pe_ratio DECIMAL(20,6) NULL AFTER market_cap"),
            ("stocks", "pb_ratio", "ALTER TABLE stocks ADD COLUMN pb_ratio DECIMAL(20,6) NULL AFTER pe_ratio"),
        ]
        with self.conn.cursor() as cursor:
            for table, column, alter_sql in alter_statements:
                try:
                    cursor.execute(f"SELECT `{column}` FROM `{table}` LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass

    # ------------------------------------------------------------------
    # Stock Pool Tables
    # ------------------------------------------------------------------

    def init_stock_pool_schema(self):
        """初始化股票池表结构"""
        with self.conn.cursor() as cursor:
            # 股票池主表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_pools (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
                    pool_type VARCHAR(32) NOT NULL COMMENT '池类型: best/major_index/industry_top5/recent_ipo_2y/all_etf',
                    code VARCHAR(32) NOT NULL COMMENT '股票代码',
                    name VARCHAR(255) NULL COMMENT '股票名称',
                    market_cap DECIMAL(28,2) NULL COMMENT '市值',
                    price DECIMAL(20,6) NULL COMMENT '价格',
                    pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率',
                    turnover DECIMAL(28,2) NULL COMMENT '成交额',
                    volume BIGINT NULL COMMENT '成交量',
                    listing_date DATE NULL COMMENT '上市日期',
                    days_since_listing INT NULL COMMENT '上市天数',
                    index_code VARCHAR(32) NULL COMMENT '所属指数代码',
                    index_name VARCHAR(255) NULL COMMENT '所属指数名称',
                    industry_code VARCHAR(64) NULL COMMENT '所属行业代码',
                    industry_name VARCHAR(128) NULL COMMENT '所属行业名称',
                    rank_in_industry INT NULL COMMENT '行业内排名',
                    extra_data JSON NULL COMMENT '额外数据',
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_pool_market_type_code (market, pool_type, code),
                    KEY idx_pool_market (market),
                    KEY idx_pool_type (pool_type),
                    KEY idx_pool_market_cap (market_cap),
                    KEY idx_pool_listing_date (listing_date),
                    KEY idx_pool_industry (industry_code),
                    KEY idx_pool_index (index_code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='股票池数据表'
                """
            )

            # 股票池更新记录表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_pool_updates (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场',
                    pool_type VARCHAR(32) NOT NULL COMMENT '池类型',
                    update_time DATETIME(6) NOT NULL COMMENT '更新时间',
                    stock_count INT NOT NULL DEFAULT 0 COMMENT '股票数量',
                    status VARCHAR(16) NOT NULL DEFAULT 'success' COMMENT '状态: success/failed',
                    error_msg TEXT NULL COMMENT '错误信息',
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    KEY idx_updates_market_type (market, pool_type),
                    KEY idx_updates_time (update_time)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='股票池更新记录'
                """
            )

    # ------------------------------------------------------------------
    # Stock Pool Operations
    # ------------------------------------------------------------------

    def upsert_stock_pool(self, market: str, pool_type: str, stocks: List[dict]):
        """
        插入或更新股票池数据

        Args:
            market: HK/US/A
            pool_type: best/major_index/industry_top5/recent_ipo_2y/all_etf
            stocks: 股票列表
        """
        if not stocks:
            return

        rows = []
        for item in stocks:
            rows.append(
                (
                    market,
                    pool_type,
                    str(item.get("code") or "").strip(),
                    item.get("name"),
                    item.get("market_cap"),
                    item.get("price"),
                    item.get("pe_ratio"),
                    item.get("turnover"),
                    item.get("volume"),
                    item.get("listing_date"),
                    item.get("days_since_listing"),
                    item.get("index_code"),
                    item.get("index_name"),
                    item.get("industry_code"),
                    item.get("industry_name"),
                    item.get("rank") or item.get("rank_in_industry"),
                    json.dumps(item.get("extra_data")) if item.get("extra_data") else None,
                )
            )

        rows = [r for r in rows if r[2]]  # 过滤空代码
        if not rows:
            return

        sql = """
            INSERT INTO stock_pools
                (market, pool_type, code, name, market_cap, price, pe_ratio,
                 turnover, volume, listing_date, days_since_listing,
                 index_code, index_name, industry_code, industry_name,
                 rank_in_industry, extra_data)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                market_cap=VALUES(market_cap),
                price=VALUES(price),
                pe_ratio=VALUES(pe_ratio),
                turnover=VALUES(turnover),
                volume=VALUES(volume),
                listing_date=VALUES(listing_date),
                days_since_listing=VALUES(days_since_listing),
                index_code=VALUES(index_code),
                index_name=VALUES(index_name),
                industry_code=VALUES(industry_code),
                industry_name=VALUES(industry_name),
                rank_in_industry=VALUES(rank_in_industry),
                extra_data=VALUES(extra_data)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_stock_pool(
        self, market: str, pool_type: str, limit: Optional[int] = None
    ) -> List[dict]:
        """
        获取股票池数据

        Args:
            market: HK/US/A
            pool_type: best/major_index/industry_top5/recent_ipo_2y/all_etf
            limit: 限制返回数量

        Returns:
            股票列表
        """
        sql = """
            SELECT code, name, market_cap, price, pe_ratio, turnover, volume,
                   listing_date, days_since_listing, index_code, index_name,
                   industry_code, industry_name, rank_in_industry, extra_data,
                   created_at, updated_at
            FROM stock_pools
            WHERE market=%s AND pool_type=%s
            ORDER BY market_cap DESC
        """
        if limit:
            sql += f" LIMIT {int(limit)}"

        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, pool_type))
            rows = cursor.fetchall()

        result = []
        for row in rows:
            result.append({
                "code": row[0],
                "name": row[1],
                "market_cap": float(row[2]) if row[2] else None,
                "price": float(row[3]) if row[3] else None,
                "pe_ratio": float(row[4]) if row[4] else None,
                "turnover": float(row[5]) if row[5] else None,
                "volume": int(row[6]) if row[6] else None,
                "listing_date": str(row[7]) if row[7] else None,
                "days_since_listing": int(row[8]) if row[8] else None,
                "index_code": row[9],
                "index_name": row[10],
                "industry_code": row[11],
                "industry_name": row[12],
                "rank_in_industry": int(row[13]) if row[13] else None,
                "extra_data": json.loads(row[14]) if row[14] else None,
                "created_at": str(row[15]) if row[15] else None,
                "updated_at": str(row[16]) if row[16] else None,
            })

        return result

    def record_pool_update(
        self, market: str, pool_type: str, stock_count: int, status: str = "success", error_msg: Optional[str] = None
    ):
        """记录股票池更新"""
        sql = """
            INSERT INTO stock_pool_updates
                (market, pool_type, update_time, stock_count, status, error_msg)
            VALUES (%s, %s, NOW(), %s, %s, %s)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, pool_type, stock_count, status, error_msg))

    def get_pool_last_update(self, market: str, pool_type: str) -> Optional[dict]:
        """获取股票池最后更新时间"""
        sql = """
            SELECT update_time, stock_count, status
            FROM stock_pool_updates
            WHERE market=%s AND pool_type=%s
            ORDER BY update_time DESC
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market, pool_type))
            row = cursor.fetchone()
            if row:
                return {
                    "update_time": str(row[0]),
                    "stock_count": int(row[1]),
                    "status": row[2],
                }
        return None
