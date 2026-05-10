#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MySQL 存储层

设计要点：
- stocks：股票主数据，(market, code) 唯一约束
- ema_breakout_signals_{timeframe}：按 timeframe 分表，每张表只存一种周期的信号
- screening_results：筛选结果
- 不再存储 K 线数据（K 线在内存中使用）
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
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


DEFAULT_RULE_MARKETS = ("HK", "US", "A")


DEFAULT_RULE_METADATA = (
    ("market_cap_range", "市值范围", "filter", "MarketCapFilter",
     {"min_cap": None, "max_cap": None}, False, 10, "按市值上下限筛选"),
    ("avg_daily_volume_range", "每日平均交易量范围", "filter", "AvgDailyVolumeFilter",
     {"min_volume": None, "max_volume": None}, False, 20, "按 K 线计算每日平均交易量"),
    ("price_range", "价格范围", "filter", "PriceFilter",
     {"min_price": None, "max_price": None}, False, 30, "按最新收盘价筛选"),
    ("pe_range", "PE 范围", "filter", "PEFilter",
     {"min_pe": None, "max_pe": None, "allow_negative": False}, False, 40, "按 PE 上下限筛选"),
    ("profitability", "公司盈利", "filter", "ProfitabilityFilter",
     {"require_profitable": True}, False, 50, "要求 PE 为正"),
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
        self.init_rule_schema()

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
                   current_stock_code, current_stock_name, params_json, check_date,
                   created_at, updated_at
            FROM screening_tasks WHERE task_id=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (task_id,))
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
