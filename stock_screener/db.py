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
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


DEFAULT_RULE_METADATA = (
    ("market_cap_range", "市值范围", "filter", "MarketCapFilter",
     {"min_cap": None, "max_cap": None}, True, 10, "按市值上下限筛选"),
    ("avg_daily_volume_range", "每日平均交易量范围", "filter", "AvgDailyVolumeFilter",
     {"min_volume": None, "max_volume": None}, True, 20, "按 K 线计算每日平均交易量"),
    ("price_range", "价格范围", "filter", "PriceFilter",
     {"min_price": None, "max_price": None}, True, 30, "按最新收盘价筛选"),
    ("pe_range", "PE 范围", "filter", "PEFilter",
     {"min_pe": None, "max_pe": None, "allow_negative": False}, True, 40, "按 PE 上下限筛选"),
    ("profitability", "公司盈利", "filter", "ProfitabilityFilter",
     {"require_profitable": True}, True, 50, "要求 PE 为正"),
    ("zuoyi_signal", "左一战法", "strategy", "ZuoYiStrategizer",
     {"signal_window": 15, "include_bullish": True, "include_bearish": True}, True, 110,
     "当前周期15根K线内左一战法看涨/看跌信号"),
    ("ema_breakout", "EMA 突破", "strategy", "EMABreakoutStrategizer",
     {"ema_short": 10, "ema_long": 150}, True, 120, "EMA 短线向上突破长线"),
    ("rsi_oversold", "RSI 超卖", "strategy", "RSIOversoldStrategizer",
     {"period": 14, "threshold": 30.0}, True, 130, "RSI 低于等于阈值"),
    ("rsi_overbought", "RSI 超买", "strategy", "RSIOverboughtStrategizer",
     {"period": 14, "threshold": 70.0}, True, 140, "RSI 高于等于阈值"),
    ("volume_spike_prior3", "放量超前三日", "strategy", "TodayVolumeExceedsPrior3MaxStrategizer",
     {}, True, 150, "当日成交量大于前三日最大值"),
    ("daily_drop_6_65", "当日跌 6%~6.5%", "strategy", "DailyDrop6To65Strategizer",
     {"pct_min": -6.5, "pct_max": -6.0}, True, 160, "当日跌幅在指定区间"),
    ("daily_rise_4_45", "当日涨 4%~4.5%", "strategy", "DailyRise4To45Strategizer",
     {"pct_min": 4.0, "pct_max": 4.5}, True, 170, "当日涨幅在指定区间"),
)


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
                    UNIQUE KEY uk_screening_market_code_date (market, code, check_date),
                    KEY idx_screening_task_id (task_id),
                    KEY idx_screening_check_date (check_date),
                    KEY idx_screening_is_passed (is_passed),
                    KEY idx_screening_market_date (market, check_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )

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

    def init_web_schema(self):
        """初始化 Web、Agent、K 线缓存和 artifact 相关表。"""
        self.init_schema("1d")
        self.init_stock_pool_schema()
        self.init_sector_schema()
        self.init_signal_analysis_schema()
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
                    UNIQUE KEY uk_screening_run_lock_scope (run_date, market, timeframe),
                    UNIQUE KEY uk_screening_run_lock_id (lock_id),
                    KEY idx_screening_run_lock_job (job_id),
                    KEY idx_screening_run_lock_status (status, run_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='全市场筛选每日分布式锁'
                """
            )

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
                "params_json": _decode_json_field(row[11], {}),
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
                    WHEN 'industry' THEN 1
                    WHEN 'index' THEN 2
                    WHEN 'ipo' THEN 3
                    WHEN 'etf' THEN 4
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
            "summary": _decode_json_field(row[12], {}),
            "created_at": str(row[13]) if row[13] else None,
            "updated_at": str(row[14]) if row[14] else None,
            "finished_at": str(row[15]) if row[15] else None,
        }

    def get_screening_run_locks(self, run_date: date, markets: List[str], timeframe: str) -> List[dict]:
        if not markets:
            return []
        placeholders = ",".join(["%s"] * len(markets))
        sql = f"""
            SELECT lock_id, run_date, market, timeframe, status, job_id, task_id,
                   agent_id, claimed_at, heartbeat_at, completed_at, error_message
            FROM screening_run_locks
            WHERE run_date=%s AND timeframe=%s AND market IN ({placeholders})
            ORDER BY market
        """
        params: List[Any] = [run_date, timeframe, *markets]
        with self.conn.cursor() as cursor:
            cursor.execute(sql, params)
            rows = cursor.fetchall() or []
        return [
            {
                "lock_id": row[0],
                "run_date": str(row[1]) if row[1] else None,
                "market": row[2],
                "timeframe": row[3],
                "status": row[4],
                "job_id": row[5],
                "task_id": row[6],
                "agent_id": row[7],
                "claimed_at": str(row[8]) if row[8] else None,
                "heartbeat_at": str(row[9]) if row[9] else None,
                "completed_at": str(row[10]) if row[10] else None,
                "error_message": row[11],
            }
            for row in rows
        ]

    def create_screening_run_locks(self, job_id: str, run_date: date, markets: List[str], timeframe: str) -> None:
        rows = [(secrets.token_hex(16), run_date, market, timeframe, "queued", job_id) for market in markets]
        if not rows:
            return
        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO screening_run_locks
                    (lock_id, run_date, market, timeframe, status, job_id)
                VALUES (%s,%s,%s,%s,%s,%s)
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
                SELECT run_id, user_id, market, code, normalized_code, timeframe,
                       status, created_at
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
                "created_at": str(row[6]) if row[6] else None,
            }
            for row in screening_rows
        ]
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
                "status": row[6],
                "created_at": str(row[7]) if row[7] else None,
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
            return int(expired_locks + cursor.rowcount)

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
    # Single stock runs
    # ------------------------------------------------------------------

    def create_single_stock_run(self, item: dict) -> None:
        sql = """
            INSERT INTO single_stock_runs
                (run_id, user_id, market, code, normalized_code, timeframe, status)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                user_id=VALUES(user_id),
                market=VALUES(market),
                code=VALUES(code),
                normalized_code=VALUES(normalized_code),
                timeframe=VALUES(timeframe),
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
                   data_source, status, warnings_json, ai_analysis_json, result_json, agent_id,
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
        result_json = _decode_json_field(row[11], {})
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
            "data_source": row[7],
            "status": row[8],
            "warnings": _decode_json_field(row[9], []),
            "ai_analysis": _decode_json_field(row[10], None),
            "result_json": result_json,
            "agent_id": row[12],
            "claimed_at": str(row[13]) if row[13] else None,
            "heartbeat_at": str(row[14]) if row[14] else None,
            "error_message": row[15],
            "created_at": str(row[16]) if row[16] else None,
            "finished_at": str(row[17]) if row[17] else None,
            "rule_details": self.get_single_stock_rule_details(row[0]),
        }
        if isinstance(result_json, dict):
            for key, value in result_json.items():
                item.setdefault(key, value)
        return item

    def list_single_stock_runs(self, limit: int = 50) -> List[dict]:
        sql = """
            SELECT run_id, user_id, market, code, normalized_code, timeframe, passed,
                   data_source, status, warnings_json, ai_analysis_json, result_json,
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
            result_json = _decode_json_field(row[11], {})
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
                "data_source": row[7],
                "status": row[8],
                "warnings": _decode_json_field(row[9], []),
                "ai_analysis": _decode_json_field(row[10], None),
                "agent_id": row[12],
                "claimed_at": str(row[13]) if row[13] else None,
                "heartbeat_at": str(row[14]) if row[14] else None,
                "error_message": row[15],
                "created_at": str(row[16]) if row[16] else None,
                "finished_at": str(row[17]) if row[17] else None,
            }
            if isinstance(result_json, dict):
                item["name"] = result_json.get("name")
                item["sector"] = result_json.get("sector")
                item["industry"] = result_json.get("industry")
            result.append(item)
        return result

    def complete_single_stock_run_from_agent(self, run_id: str, result: dict, rule_details: Iterable[dict]) -> None:
        sql = """
            UPDATE single_stock_runs
            SET passed=%s, status=%s, data_source=%s, warnings_json=%s,
                ai_analysis_json=%s, result_json=%s, finished_at=%s
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_rule_chains (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场: HK/US/A',
                    chain_key VARCHAR(64) NOT NULL COMMENT '规则链键',
                    chain_name VARCHAR(128) NOT NULL COMMENT '规则链名称',
                    expression_json JSON NOT NULL COMMENT '规则链 JSON DSL',
                    enabled TINYINT(1) NOT NULL DEFAULT 1 COMMENT '是否启用',
                    priority INT NOT NULL DEFAULT 100 COMMENT '优先级，越小越优先',
                    description VARCHAR(512) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_rule_chains_market_key (market, chain_key),
                    KEY idx_rule_chains_market_enabled (market, enabled, priority)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                COMMENT='筛选规则使用链'
                """
            )
        self.seed_default_screening_rules()

    def seed_default_screening_rules(self):
        """写入默认规则配置；已有配置保持不变。"""
        metadata_rows = []
        for market in DEFAULT_RULE_MARKETS:
            for (
                rule_key,
                rule_name,
                rule_type,
                implementation,
                params,
                enabled,
                display_order,
                description,
            ) in DEFAULT_RULE_METADATA:
                metadata_rows.append((
                    market,
                    rule_key,
                    rule_name,
                    rule_type,
                    implementation,
                    json.dumps(params, ensure_ascii=False),
                    1 if enabled else 0,
                    display_order,
                    description,
                ))

        chain_rows = [
            (
                market,
                "default_zuoyi_and_other",
                "左一战法与其他策略默认链",
                json.dumps(DEFAULT_RULE_CHAIN_EXPRESSION, ensure_ascii=False),
                1,
                100,
                "启用硬筛选全部通过 && 左一战法命中 && 至少一个其他策略命中",
            )
            for market in DEFAULT_RULE_MARKETS
        ]

        with self.conn.cursor() as cursor:
            cursor.executemany(
                """
                INSERT IGNORE INTO screening_rule_metadata
                    (market, rule_key, rule_name, rule_type, implementation,
                     params_json, enabled, display_order, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                metadata_rows,
            )
            cursor.executemany(
                """
                INSERT IGNORE INTO screening_rule_chains
                    (market, chain_key, chain_name, expression_json, enabled, priority, description)
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                """,
                chain_rows,
            )

    def get_screening_rule_metadata(self, market: str) -> List[dict]:
        """读取某个市场的所有原子规则元数据。"""
        sql = """
            SELECT market, rule_key, rule_name, rule_type, implementation,
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
                "implementation": row[4],
                "params_json": _decode_json_field(row[5], {}),
                "enabled": bool(row[6]),
                "display_order": int(row[7] or 0),
                "description": row[8],
            }
            for row in rows
        ]

    def get_active_screening_rule_chain(self, market: str) -> Optional[dict]:
        """读取某个市场优先级最高的启用规则链。"""
        sql = """
            SELECT market, chain_key, chain_name, expression_json,
                   enabled, priority, description
            FROM screening_rule_chains
            WHERE market=%s AND enabled=1
            ORDER BY priority ASC, id ASC
            LIMIT 1
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market,))
            row = cursor.fetchone()
        if not row:
            return None
        return {
            "market": row[0],
            "chain_key": row[1],
            "chain_name": row[2],
            "expression_json": _decode_json_field(row[3], {}),
            "enabled": bool(row[4]),
            "priority": int(row[5] or 100),
            "description": row[6],
        }

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
                    hot_sectors JSON NULL COMMENT '识别到的热点板块',
                    hot_sector_mark VARCHAR(32) NULL COMMENT '重点/相关/观察/无明确关联/未知',
                    matched_hot_sectors JSON NULL COMMENT '匹配到的热点板块',
                    hot_sector_relevance VARCHAR(64) NULL COMMENT '热点板块关联度',
                    hot_sector_reason TEXT NULL COMMENT '热点板块匹配理由',
                    hot_sector_sources JSON NULL COMMENT '热点板块来源',
                    source_urls JSON NULL COMMENT '信息来源 URL',
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
                ("hot_sectors", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sectors JSON NULL COMMENT '识别到的热点板块' AFTER company_events"),
                ("hot_sector_mark", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_mark VARCHAR(32) NULL COMMENT '重点/相关/观察/无明确关联/未知' AFTER hot_sectors"),
                ("matched_hot_sectors", "ALTER TABLE screening_signal_analysis ADD COLUMN matched_hot_sectors JSON NULL COMMENT '匹配到的热点板块' AFTER hot_sector_mark"),
                ("hot_sector_relevance", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_relevance VARCHAR(64) NULL COMMENT '热点板块关联度' AFTER matched_hot_sectors"),
                ("hot_sector_reason", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_reason TEXT NULL COMMENT '热点板块匹配理由' AFTER hot_sector_relevance"),
                ("hot_sector_sources", "ALTER TABLE screening_signal_analysis ADD COLUMN hot_sector_sources JSON NULL COMMENT '热点板块来源' AFTER hot_sector_reason"),
            ]
            for column, alter_sql in signal_analysis_alters:
                try:
                    cursor.execute(f"SELECT `{column}` FROM screening_signal_analysis LIMIT 1")
                except Exception:
                    try:
                        cursor.execute(alter_sql)
                    except Exception:
                        pass

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
                _json_or_none(item.get("hot_sectors")),
                item.get("hot_sector_mark"),
                _json_or_none(item.get("matched_hot_sectors")),
                item.get("hot_sector_relevance"),
                item.get("hot_sector_reason"),
                _json_or_none(item.get("hot_sector_sources")),
                _json_or_none(item.get("source_urls")),
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
                 hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                 hot_sector_reason, hot_sector_sources, source_urls, model, raw_response,
                 error_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                hot_sectors=VALUES(hot_sectors),
                hot_sector_mark=VALUES(hot_sector_mark),
                matched_hot_sectors=VALUES(matched_hot_sectors),
                hot_sector_relevance=VALUES(hot_sector_relevance),
                hot_sector_reason=VALUES(hot_sector_reason),
                hot_sector_sources=VALUES(hot_sector_sources),
                source_urls=VALUES(source_urls),
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
                   hot_sectors, hot_sector_mark, matched_hot_sectors, hot_sector_relevance,
                   hot_sector_reason, hot_sector_sources, source_urls, model, raw_response,
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
                "hot_sectors": _decode_json_field(row[15], []),
                "hot_sector_mark": row[16],
                "matched_hot_sectors": _decode_json_field(row[17], []),
                "hot_sector_relevance": row[18],
                "hot_sector_reason": row[19],
                "hot_sector_sources": _decode_json_field(row[20], []),
                "source_urls": _decode_json_field(row[21], []),
                "model": row[22],
                "raw_response": _decode_json_field(row[23], None),
                "error_message": row[24],
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
                    pool_type VARCHAR(32) NOT NULL COMMENT '池类型: best/index/industry/ipo/etf',
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
            pool_type: best/index/industry/ipo/etf
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
            pool_type: best/index/industry/ipo/etf
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
