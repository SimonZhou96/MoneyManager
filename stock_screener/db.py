from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable, List, Optional

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
                    source VARCHAR(32) NULL,
                    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6),
                    PRIMARY KEY (id),
                    UNIQUE KEY uk_stocks_market_code (market, code),
                    KEY idx_stocks_market (market),
                    KEY idx_stocks_code (code)
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
                    volume DECIMAL(28,6) NULL,
                    turnover DECIMAL(28,6) NULL,
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
                    source or item.get("source"),
                )
            )
        rows = [r for r in rows if r[1]]
        if not rows:
            return

        sql = """
            INSERT INTO stocks
                (market, code, name, exchange, currency, lot_size, status, listing_date, delisting_date, source)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                name=VALUES(name),
                exchange=VALUES(exchange),
                currency=VALUES(currency),
                lot_size=VALUES(lot_size),
                status=VALUES(status),
                listing_date=VALUES(listing_date),
                delisting_date=VALUES(delisting_date),
                source=VALUES(source)
        """
        with self.conn.cursor() as cursor:
            cursor.executemany(sql, rows)

    def get_stocks(self, market: str) -> List[dict]:
        with self.conn.cursor() as cursor:
            cursor.execute(
                "SELECT code, name FROM stocks WHERE market=%s ORDER BY code",
                (market,),
            )
            rows = cursor.fetchall() or []
            return [{"code": row[0], "name": row[1]} for row in rows]

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
                    float(row.get("volume")) if pd.notna(row.get("volume")) else None,
                    float(row.get("turnover")) if pd.notna(row.get("turnover")) else None,
                    adj_type,
                    source,
                )
            )

        if not rows:
            return

        sql = """
            INSERT INTO kline_daily
                (market, code, trade_date, open, high, low, close, volume, turnover, adj_type, source)
            VALUES
                (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                open=VALUES(open),
                high=VALUES(high),
                low=VALUES(low),
                close=VALUES(close),
                volume=VALUES(volume),
                turnover=VALUES(turnover),
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
