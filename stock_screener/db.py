import os
import sqlite3
from datetime import datetime
from typing import Iterable, List, Optional

import pandas as pd


class MarketDatabase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        db_dir = os.path.dirname(db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.execute("PRAGMA journal_mode=WAL;")
        self.conn.execute("PRAGMA synchronous=NORMAL;")

    def close(self):
        self.conn.close()

    def init_schema(self):
        cursor = self.conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS stocks (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                name TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, code)
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS kline_daily (
                market TEXT NOT NULL,
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (market, code, date)
            )
            """
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_kline_market_code_date ON kline_daily (market, code, date)"
        )
        self.conn.commit()

    def stock_count(self, market: str) -> int:
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(1) FROM stocks WHERE market = ?", (market,))
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def upsert_stocks(self, market: str, stocks: Iterable[dict]):
        now = datetime.utcnow().isoformat()
        rows = [(market, item["code"], item.get("name"), now) for item in stocks]
        if not rows:
            return
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT INTO stocks (market, code, name, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(market, code) DO UPDATE SET
                name=excluded.name,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        self.conn.commit()

    def get_stocks(self, market: str) -> List[dict]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT code, name FROM stocks WHERE market = ? ORDER BY code", (market,)
        )
        rows = cursor.fetchall()
        return [{"code": row[0], "name": row[1]} for row in rows]

    def last_kline_date(self, market: str, code: str) -> Optional[str]:
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT MAX(date) FROM kline_daily WHERE market = ? AND code = ?",
            (market, code),
        )
        row = cursor.fetchone()
        return row[0] if row and row[0] else None

    def upsert_klines(self, market: str, code: str, df: pd.DataFrame):
        if df is None or df.empty:
            return
        now = datetime.utcnow().isoformat()
        rows = []
        for _, row in df.iterrows():
            rows.append(
                (
                    market,
                    code,
                    pd.to_datetime(row["date"]).strftime("%Y-%m-%d"),
                    float(row.get("open")) if pd.notna(row.get("open")) else None,
                    float(row.get("high")) if pd.notna(row.get("high")) else None,
                    float(row.get("low")) if pd.notna(row.get("low")) else None,
                    float(row.get("close")) if pd.notna(row.get("close")) else None,
                    float(row.get("volume")) if pd.notna(row.get("volume")) else None,
                    now,
                )
            )
        cursor = self.conn.cursor()
        cursor.executemany(
            """
            INSERT INTO kline_daily (market, code, date, open, high, low, close, volume, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(market, code, date) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                updated_at=excluded.updated_at
            """,
            rows,
        )
        self.conn.commit()
