from __future__ import annotations

import json
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


class MarketDatabase:
    """
    MySQL 存储层（替代 SQLite）。

    设计要点：
    - `stocks`：股票主数据，(market, code) 唯一约束
    - `kline_daily`：日 K 明细，(market, code, trade_date, adj_type) 唯一约束，便于幂等 upsert
    - `created_at` / `updated_at`：由数据库自动维护（微秒级）
    """

    def __init__(self, config: MySqlConfig):
        if pymysql is None:  # pragma: no cover
            raise RuntimeError(
                "缺少 MySQL 驱动 PyMySQL，请先安装依赖。"
            ) from _PYMYSQL_IMPORT_ERROR

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

    def init_schema(self):
        with self.conn.cursor() as cursor:
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
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS kline_daily (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL,
                    code VARCHAR(32) NOT NULL,
                    trade_date DATE NOT NULL,
                    open DECIMAL(20,6) NULL,
                    high DECIMAL(20,6) NULL,
                    low DECIMAL(20,6) NULL,
                    close DECIMAL(20,6) NULL,
                    last_close DECIMAL(20,6) NULL,
                    volume DECIMAL(28,6) NULL,
                    turnover DECIMAL(28,6) NULL,
                    turnover_rate DECIMAL(20,8) NULL,
                    change_rate DECIMAL(20,8) NULL,
                    pe_ratio DECIMAL(20,6) NULL,
                    adj_type VARCHAR(16) NOT NULL DEFAULT 'qfq',
                    source VARCHAR(32) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_kline_market_code_date_adj (market, code, trade_date, adj_type),
                    KEY idx_kline_market_code_date (market, code, trade_date),
                    KEY idx_kline_market_date (market, trade_date),
                    KEY idx_kline_code_date (code, trade_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """
            )
            # EMA突破信号表：记录每日每只股票的EMA突破策略判断结果
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS ema_breakout_signals (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场（HK/US）',
                    code VARCHAR(32) NOT NULL COMMENT '股票代码',
                    name VARCHAR(255) NULL COMMENT '股票名称',
                    check_date DATE NOT NULL COMMENT '检查日期（执行策略判断的日期）',
                    result_type VARCHAR(32) NOT NULL COMMENT '结果类型枚举值',
                    is_satisfied TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否满足突破条件',
                    breakout_date DATE NULL COMMENT '突破发生的日期',
                    ema10 DECIMAL(20,6) NULL COMMENT '最新EMA10值',
                    ema150 DECIMAL(20,6) NULL COMMENT '最新EMA150值',
                    close_price DECIMAL(20,6) NULL COMMENT '最新收盘价',
                    data_rows INT NULL COMMENT '用于计算的K线数据行数',
                    result_desc VARCHAR(255) NULL COMMENT '结果描述',
                    sector VARCHAR(128) NULL COMMENT '板块名称',
                    industry VARCHAR(128) NULL COMMENT '行业名称',
                    market_cap DECIMAL(28,2) NULL COMMENT '市值',
                    pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率',
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_ema_market_code_date (market, code, check_date),
                    KEY idx_ema_check_date (check_date),
                    KEY idx_ema_result_type (result_type),
                    KEY idx_ema_is_satisfied (is_satisfied),
                    KEY idx_ema_market_date (market, check_date),
                    KEY idx_ema_sector (sector),
                    KEY idx_ema_industry (industry)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='EMA突破策略信号记录表'
                """
            )
            # 筛选结果表：记录每只股票的筛选器链执行结果
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS screening_results (
                    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
                    market VARCHAR(8) NOT NULL COMMENT '市场（HK/US）',
                    code VARCHAR(32) NOT NULL COMMENT '股票代码',
                    name VARCHAR(255) NULL COMMENT '股票名称',
                    check_date DATE NOT NULL COMMENT '筛选日期',
                    is_passed TINYINT(1) NOT NULL DEFAULT 0 COMMENT '是否通过所有筛选条件',
                    filter_summary VARCHAR(512) NULL COMMENT '筛选结果摘要',
                    filter_details JSON NULL COMMENT '各筛选器详细结果(JSON)',
                    sector VARCHAR(128) NULL COMMENT '板块名称',
                    industry VARCHAR(128) NULL COMMENT '行业名称',
                    market_cap DECIMAL(28,2) NULL COMMENT '市值',
                    pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率',
                    close_price DECIMAL(20,6) NULL COMMENT '最新收盘价',
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_screening_market_code_date (market, code, check_date),
                    KEY idx_screening_check_date (check_date),
                    KEY idx_screening_is_passed (is_passed),
                    KEY idx_screening_market_date (market, check_date)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='股票筛选结果记录表'
                """
            )

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
                (market, code, name, exchange, currency, lot_size, status, listing_date, delisting_date,
                 sector, sector_code, industry, industry_code, market_cap, pe_ratio, pb_ratio, source)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                        "code": row[0],
                        "name": row[1],
                        "sector": row[2],
                        "sector_code": row[3],
                        "industry": row[4],
                        "industry_code": row[5],
                        "market_cap": float(row[6]) if row[6] else None,
                        "pe_ratio": float(row[7]) if row[7] else None,
                        "pb_ratio": float(row[8]) if row[8] else None,
                    }
                    for row in rows
                ]
            else:
                cursor.execute(
                    "SELECT code, name, sector, industry FROM stocks WHERE market=%s ORDER BY code",
                    (market,),
                )
                rows = cursor.fetchall() or []
                return [
                    {"code": row[0], "name": row[1], "sector": row[2], "industry": row[3]}
                    for row in rows
                ]

    def last_kline_date(self, market: str, code: str, adj_type: str = "qfq") -> Optional[str]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT MAX(trade_date) FROM kline_daily WHERE market=%s AND code=%s AND adj_type=%s",
                (market, code, adj_type),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            if isinstance(row[0], date):
                return row[0].strftime("%Y-%m-%d")
            return str(row[0])

    def upsert_klines(
        self,
        market: str,
        code: str,
        df: pd.DataFrame,
        *,
        adj_type: str = "qfq",
        source: Optional[str] = None,
    ):
        if df is None or df.empty:
            return

        rows = []
        for _, row in df.iterrows():
            trade_date = pd.to_datetime(row.get("date")).date() if pd.notna(row.get("date")) else None
            if trade_date is None:
                continue
            rows.append(
                (
                    market,
                    code,
                    trade_date,
                    float(row.get("open")) if pd.notna(row.get("open")) else None,
                    float(row.get("high")) if pd.notna(row.get("high")) else None,
                    float(row.get("low")) if pd.notna(row.get("low")) else None,
                    float(row.get("close")) if pd.notna(row.get("close")) else None,
                    float(row.get("last_close")) if pd.notna(row.get("last_close")) else None,
                    float(row.get("volume")) if pd.notna(row.get("volume")) else None,
                    float(row.get("turnover")) if pd.notna(row.get("turnover")) else None,
                    float(row.get("turnover_rate")) if pd.notna(row.get("turnover_rate")) else None,
                    float(row.get("change_rate")) if pd.notna(row.get("change_rate")) else None,
                    float(row.get("pe_ratio")) if pd.notna(row.get("pe_ratio")) else None,
                    adj_type,
                    source,
                )
            )

        if not rows:
            return

        sql = """
            INSERT INTO kline_daily
                (
                    market, code, trade_date,
                    open, high, low, close, last_close,
                    volume, turnover, turnover_rate, change_rate, pe_ratio,
                    adj_type, source
                )
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open),
                high=VALUES(high),
                low=VALUES(low),
                close=VALUES(close),
                last_close=VALUES(last_close),
                volume=VALUES(volume),
                turnover=VALUES(turnover),
                turnover_rate=VALUES(turnover_rate),
                change_rate=VALUES(change_rate),
                pe_ratio=VALUES(pe_ratio),
                source=VALUES(source)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def prune_old_klines(self, market: str, code: str, cutoff_date: date, adj_type: str = "qfq") -> int:
        with self.conn.cursor() as cursor:
            cursor.execute(
                "DELETE FROM kline_daily WHERE market=%s AND code=%s AND adj_type=%s AND trade_date < %s",
                (market, code, adj_type, cutoff_date),
            )
            return int(cursor.rowcount or 0)

    def get_klines(
        self,
        market: str,
        code: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        adj_type: str = "qfq",
    ) -> pd.DataFrame:
        """
        查询K线数据
        
        Args:
            market: 市场（HK/US）
            code: 股票代码
            start_date: 开始日期（YYYY-MM-DD），可选
            end_date: 结束日期（YYYY-MM-DD），可选
            adj_type: 复权类型，默认 qfq（前复权）
            
        Returns:
            pd.DataFrame with columns: date, open, high, low, close, volume, turnover, etc.
        """
        query = """
            SELECT 
                trade_date AS date,
                open, high, low, close, last_close,
                volume, turnover, turnover_rate, change_rate, pe_ratio
            FROM kline_daily
            WHERE market=%s AND code=%s AND adj_type=%s
        """
        params = [market, code, adj_type]
        
        if start_date:
            query += " AND trade_date >= %s"
            params.append(start_date)
        if end_date:
            query += " AND trade_date <= %s"
            params.append(end_date)
        
        query += " ORDER BY trade_date ASC"
        
        with self.conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()
            
            if not rows:
                return pd.DataFrame()
            
            # 转换为DataFrame
            columns = [
                "date", "open", "high", "low", "close", "last_close",
                "volume", "turnover", "turnover_rate", "change_rate", "pe_ratio"
            ]
            df = pd.DataFrame(rows, columns=columns)
            
            # 确保date列是datetime类型
            df["date"] = pd.to_datetime(df["date"])
            
            return df

    def upsert_ema_breakout_signal(
        self,
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
        """
        插入或更新 EMA 突破信号记录
        
        Args:
            market: 市场（HK/US）
            code: 股票代码
            check_date: 检查日期
            result_type: 结果类型枚举值
            is_satisfied: 是否满足突破条件
            breakout_date: 突破发生的日期
            ema10: 最新 EMA10 值
            ema150: 最新 EMA150 值
            close_price: 最新收盘价
            data_rows: 用于计算的 K 线数据行数
            result_desc: 结果描述
            name: 股票名称
            sector: 板块名称
            industry: 行业名称
            market_cap: 市值
            pe_ratio: 市盈率
        """
        sql = """
            INSERT INTO ema_breakout_signals
                (market, code, name, check_date, result_type, is_satisfied, 
                 breakout_date, ema10, ema150, close_price, data_rows, result_desc,
                 sector, industry, market_cap, pe_ratio)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=COALESCE(VALUES(name), name),
                result_type=VALUES(result_type),
                is_satisfied=VALUES(is_satisfied),
                breakout_date=VALUES(breakout_date),
                ema10=VALUES(ema10),
                ema150=VALUES(ema150),
                close_price=VALUES(close_price),
                data_rows=VALUES(data_rows),
                result_desc=VALUES(result_desc),
                sector=COALESCE(VALUES(sector), sector),
                industry=COALESCE(VALUES(industry), industry),
                market_cap=COALESCE(VALUES(market_cap), market_cap),
                pe_ratio=COALESCE(VALUES(pe_ratio), pe_ratio)
        """
        with self.conn.cursor() as cursor:
            cursor.execute(
                sql,
                (
                    market,
                    code,
                    name,
                    check_date,
                    result_type,
                    1 if is_satisfied else 0,
                    breakout_date,
                    ema10,
                    ema150,
                    close_price,
                    data_rows,
                    result_desc,
                    sector,
                    industry,
                    market_cap,
                    pe_ratio,
                ),
            )

    def get_ema_breakout_signals(
        self,
        market: Optional[str] = None,
        check_date: Optional[date] = None,
        is_satisfied: Optional[bool] = None,
        sector: Optional[str] = None,
        industry: Optional[str] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        查询 EMA 突破信号记录
        
        Args:
            market: 市场（可选）
            check_date: 检查日期（可选）
            is_satisfied: 是否满足条件（可选）
            sector: 板块过滤（可选）
            industry: 行业过滤（可选）
            limit: 最大返回数量
            
        Returns:
            信号记录列表
        """
        query = """SELECT market, code, name, check_date, result_type, is_satisfied, breakout_date, 
                          ema10, ema150, close_price, data_rows, result_desc, sector, industry, 
                          market_cap, pe_ratio 
                   FROM ema_breakout_signals WHERE 1=1"""
        params = []
        
        if market:
            query += " AND market=%s"
            params.append(market)
        if check_date:
            query += " AND check_date=%s"
            params.append(check_date)
        if is_satisfied is not None:
            query += " AND is_satisfied=%s"
            params.append(1 if is_satisfied else 0)
        if sector:
            query += " AND sector=%s"
            params.append(sector)
        if industry:
            query += " AND industry=%s"
            params.append(industry)
        
        query += " ORDER BY check_date DESC, market, code LIMIT %s"
        params.append(limit)
        
        with self.conn.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall() or []
            return [
                {
                    "market": row[0],
                    "code": row[1],
                    "name": row[2],
                    "check_date": row[3],
                    "result_type": row[4],
                    "is_satisfied": bool(row[5]),
                    "breakout_date": row[6],
                    "ema10": float(row[7]) if row[7] else None,
                    "ema150": float(row[8]) if row[8] else None,
                    "close_price": float(row[9]) if row[9] else None,
                    "data_rows": row[10],
                    "result_desc": row[11],
                    "sector": row[12],
                    "industry": row[13],
                    "market_cap": float(row[14]) if row[14] else None,
                    "pe_ratio": float(row[15]) if row[15] else None,
                }
                for row in rows
            ]

    def upsert_screening_results(
        self,
        check_date: date,
        results: Iterable[dict],
    ):
        """
        批量插入或更新筛选结果

        Args:
            check_date: 筛选日期
            results: 筛选结果列表，每项需包含: market, code, name, is_passed,
                     filter_summary, filter_details(dict/JSON), sector, industry,
                     market_cap, pe_ratio, close_price
        """
        rows = []
        for item in results:
            filter_details = item.get("filter_details")
            if isinstance(filter_details, dict):
                filter_details_str = json.dumps(filter_details, ensure_ascii=False)
            elif isinstance(filter_details, str):
                filter_details_str = filter_details
            else:
                filter_details_str = None
            rows.append(
                (
                    item.get("market"),
                    str(item.get("code") or "").strip(),
                    item.get("name"),
                    check_date,
                    1 if item.get("is_passed") else 0,
                    item.get("filter_summary"),
                    filter_details_str,
                    item.get("sector"),
                    item.get("industry"),
                    item.get("market_cap"),
                    item.get("pe_ratio"),
                    item.get("close_price"),
                )
            )
        rows = [r for r in rows if r[1]]
        if not rows:
            return
        sql = """
            INSERT INTO screening_results
                (market, code, name, check_date, is_passed, filter_summary, filter_details,
                 sector, industry, market_cap, pe_ratio, close_price)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                is_passed=VALUES(is_passed),
                filter_summary=VALUES(filter_summary),
                filter_details=VALUES(filter_details),
                sector=VALUES(sector),
                industry=VALUES(industry),
                market_cap=VALUES(market_cap),
                pe_ratio=VALUES(pe_ratio),
                close_price=VALUES(close_price)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def update_stock_sector(
        self,
        market: str,
        code: str,
        sector: Optional[str] = None,
        sector_code: Optional[str] = None,
        industry: Optional[str] = None,
        industry_code: Optional[str] = None,
    ):
        """
        更新股票的板块/行业信息
        
        Args:
            market: 市场
            code: 股票代码
            sector: 板块名称
            sector_code: 板块代码
            industry: 行业名称
            industry_code: 行业代码
        """
        sql = """
            UPDATE stocks SET
                sector=COALESCE(%s, sector),
                sector_code=COALESCE(%s, sector_code),
                industry=COALESCE(%s, industry),
                industry_code=COALESCE(%s, industry_code)
            WHERE market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (sector, sector_code, industry, industry_code, market, code))

    def update_stock_fundamentals(
        self,
        market: str,
        code: str,
        market_cap: Optional[float] = None,
        pe_ratio: Optional[float] = None,
        pb_ratio: Optional[float] = None,
    ):
        """
        更新股票的基本面数据
        
        Args:
            market: 市场
            code: 股票代码
            market_cap: 市值
            pe_ratio: 市盈率
            pb_ratio: 市净率
        """
        sql = """
            UPDATE stocks SET
                market_cap=COALESCE(%s, market_cap),
                pe_ratio=COALESCE(%s, pe_ratio),
                pb_ratio=COALESCE(%s, pb_ratio)
            WHERE market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.execute(sql, (market_cap, pe_ratio, pb_ratio, market, code))

    def batch_update_stock_sectors(
        self,
        market: str,
        sector_data: Iterable[dict],
    ):
        """
        批量更新股票板块信息
        
        Args:
            market: 市场
            sector_data: 板块数据列表 [{"code": "...", "sector": "...", "industry": "..."}, ...]
        """
        rows = []
        for item in sector_data:
            code = str(item.get("code") or "").strip()
            if not code:
                continue
            rows.append((
                item.get("sector"),
                item.get("sector_code"),
                item.get("industry"),
                item.get("industry_code"),
                market,
                code,
            ))
        
        if not rows:
            return
        
        sql = """
            UPDATE stocks SET
                sector=COALESCE(%s, sector),
                sector_code=COALESCE(%s, sector_code),
                industry=COALESCE(%s, industry),
                industry_code=COALESCE(%s, industry_code)
            WHERE market=%s AND code=%s
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def migrate_add_sector_columns(self):
        """
        数据库迁移：为现有表添加板块相关列
        
        此方法用于在已有表结构上添加新列，可以安全地多次执行（幂等）。
        """
        alter_statements = [
            # stocks 表新增列
            ("stocks", "sector", "ALTER TABLE stocks ADD COLUMN sector VARCHAR(128) NULL COMMENT '板块名称' AFTER delisting_date"),
            ("stocks", "sector_code", "ALTER TABLE stocks ADD COLUMN sector_code VARCHAR(64) NULL COMMENT '板块代码' AFTER sector"),
            ("stocks", "industry", "ALTER TABLE stocks ADD COLUMN industry VARCHAR(128) NULL COMMENT '行业名称' AFTER sector_code"),
            ("stocks", "industry_code", "ALTER TABLE stocks ADD COLUMN industry_code VARCHAR(64) NULL COMMENT '行业代码' AFTER industry"),
            ("stocks", "market_cap", "ALTER TABLE stocks ADD COLUMN market_cap DECIMAL(28,2) NULL COMMENT '市值' AFTER industry_code"),
            ("stocks", "pe_ratio", "ALTER TABLE stocks ADD COLUMN pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率' AFTER market_cap"),
            ("stocks", "pb_ratio", "ALTER TABLE stocks ADD COLUMN pb_ratio DECIMAL(20,6) NULL COMMENT '市净率' AFTER pe_ratio"),
            # ema_breakout_signals 表新增列
            ("ema_breakout_signals", "name", "ALTER TABLE ema_breakout_signals ADD COLUMN name VARCHAR(255) NULL COMMENT '股票名称' AFTER code"),
            ("ema_breakout_signals", "sector", "ALTER TABLE ema_breakout_signals ADD COLUMN sector VARCHAR(128) NULL COMMENT '板块名称' AFTER result_desc"),
            ("ema_breakout_signals", "industry", "ALTER TABLE ema_breakout_signals ADD COLUMN industry VARCHAR(128) NULL COMMENT '行业名称' AFTER sector"),
            ("ema_breakout_signals", "market_cap", "ALTER TABLE ema_breakout_signals ADD COLUMN market_cap DECIMAL(28,2) NULL COMMENT '市值' AFTER industry"),
            ("ema_breakout_signals", "pe_ratio", "ALTER TABLE ema_breakout_signals ADD COLUMN pe_ratio DECIMAL(20,6) NULL COMMENT '市盈率' AFTER market_cap"),
        ]
        
        # 添加索引
        index_statements = [
            ("stocks", "idx_stocks_sector", "ALTER TABLE stocks ADD INDEX idx_stocks_sector (sector)"),
            ("stocks", "idx_stocks_industry", "ALTER TABLE stocks ADD INDEX idx_stocks_industry (industry)"),
            ("ema_breakout_signals", "idx_ema_sector", "ALTER TABLE ema_breakout_signals ADD INDEX idx_ema_sector (sector)"),
            ("ema_breakout_signals", "idx_ema_industry", "ALTER TABLE ema_breakout_signals ADD INDEX idx_ema_industry (industry)"),
        ]
        
        with self.conn.cursor() as cursor:
            # 检查并添加列
            for table, column, alter_sql in alter_statements:
                try:
                    cursor.execute(f"SELECT {column} FROM {table} LIMIT 1")
                except Exception:
                    # 列不存在，添加它
                    try:
                        cursor.execute(alter_sql)
                        print(f"✓ 添加列: {table}.{column}")
                    except Exception as e:
                        print(f"⚠️ 添加列失败: {table}.{column} - {e}")
            
            # 检查并添加索引
            for table, index_name, index_sql in index_statements:
                try:
                    cursor.execute(f"SHOW INDEX FROM {table} WHERE Key_name = %s", (index_name,))
                    if not cursor.fetchone():
                        cursor.execute(index_sql)
                        print(f"✓ 添加索引: {table}.{index_name}")
                except Exception as e:
                    print(f"⚠️ 添加索引失败: {table}.{index_name} - {e}")
