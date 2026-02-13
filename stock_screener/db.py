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
from typing import Any, Iterable, List, Optional

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

            # screening_results 表
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_results (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
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
                    KEY idx_screening_check_date (check_date),
                    KEY idx_screening_is_passed (is_passed),
                    KEY idx_screening_market_date (market, check_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
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
            rows.append((
                item.get("market"), str(item.get("code") or "").strip(), item.get("name"),
                check_date, 1 if item.get("is_passed") else 0,
                item.get("filter_summary"), fd_str,
                item.get("sector"), item.get("industry"),
                item.get("market_cap"), item.get("pe_ratio"), item.get("close_price"),
            ))
        rows = [r for r in rows if r[1]]
        if not rows:
            return
        sql = """
            INSERT INTO screening_results
                (market, code, name, check_date, is_passed, filter_summary, filter_details,
                 sector, industry, market_cap, pe_ratio, close_price)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name), is_passed=VALUES(is_passed),
                filter_summary=VALUES(filter_summary), filter_details=VALUES(filter_details),
                sector=VALUES(sector), industry=VALUES(industry),
                market_cap=VALUES(market_cap), pe_ratio=VALUES(pe_ratio),
                close_price=VALUES(close_price)
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
