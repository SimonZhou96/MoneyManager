"""Set-based catalog writes and durable daily execution accounting."""
from collections import Counter
from datetime import datetime
import json
import hashlib
import re
from pathlib import Path
from zoneinfo import ZoneInfo

from .names import choose_names
from .transport import BudgetExceeded

ZONE = ZoneInfo("Asia/Shanghai")


def lock_name(db):
    # MySQL named locks are server-wide; test/other databases must be independent.
    return "mm:catalog:" + hashlib.sha256(db.config.database.encode()).hexdigest()[:24]


class Repository:
    def __init__(self, db, run_date, request_limit=200):
        self.db, self.day, self.request_limit = db, run_date, request_limit
        self.sources, self.requests, self.phase = {}, 0, ""
        self.locked = False

    def start(self, force=False):
        with self.db.conn.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name(self.db),))
            if cursor.fetchone()[0] != 1:
                return False
        self.locked = True
        self.db.init_stock_pool_schema()
        sql = Path(__file__).resolve().parents[1] / "sql/021_stock_catalog_sync.sql"
        text = "\n".join(line for line in sql.read_text().splitlines() if not line.lstrip().startswith("--"))
        with self.db.conn.cursor() as cursor:
            for statement in text.split(";"):
                if statement.strip():
                    cursor.execute(statement)
            cursor.execute("SELECT status,request_count,sources_json FROM stock_catalog_sync_runs WHERE run_date=%s", (self.day,))
            row = cursor.fetchone()
            if row:
                self.requests = row[1]
                self.sources = json.loads(row[2]) if isinstance(row[2], str) else row[2]
                if row[0] in {"success", "partial", "failed"} and not force:
                    return False
                cursor.execute("UPDATE stock_catalog_sync_runs SET status='running',finished_at=NULL,error_message=NULL WHERE run_date=%s", (self.day,))
            else:
                cursor.execute("INSERT INTO stock_catalog_sync_runs (run_date,sources_json) VALUES (%s,%s)", (self.day, "{}"))
        return True

    def close(self):
        if self.locked:
            with self.db.conn.cursor() as cursor:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (lock_name(self.db),))
            self.locked = False

    def bump_attempt(self):
        with self.db.conn.cursor() as cursor:
            cursor.execute("""UPDATE stock_catalog_sync_runs SET request_count=request_count+1
                              WHERE run_date=%s AND request_count<%s""", (self.day, self.request_limit))
            if cursor.rowcount != 1:
                raise BudgetExceeded(f"Daily HTTP limit ({self.request_limit}) reached")
        self.requests += 1
        item = self.sources.setdefault(self.phase, {})
        item["requests"] = item.get("requests", 0) + 1
        self.save_sources()

    def save_sources(self):
        with self.db.conn.cursor() as cursor:
            cursor.execute("UPDATE stock_catalog_sync_runs SET sources_json=%s WHERE run_date=%s",
                           (json.dumps(self.sources, ensure_ascii=False), self.day))

    def previous_count(self, source):
        with self.db.conn.cursor() as cursor:
            cursor.execute("SELECT sources_json FROM stock_catalog_sync_runs WHERE run_date<%s ORDER BY run_date DESC LIMIT 90", (self.day,))
            for row in cursor.fetchall():
                sources = json.loads(row[0]) if isinstance(row[0], str) else row[0]
                entry = sources.get(source, {})
                if entry.get("status") in {"success", "partial"} and entry.get("seen"):
                    return entry["seen"]
        return 0

    def apply(self, market, source, records):
        if not records:
            raise ValueError("Empty source snapshot; existing catalog retained")
        if not isinstance(records, list) or any(not isinstance(record, dict) for record in records):
            raise ValueError("Invalid cached catalog snapshot shape")
        by_code = {}
        for record in records:
            if record.get("market") != market or record.get("instrument_type") not in {"股票", "ETF"}:
                raise ValueError("Invalid catalog record market/type")
            code = record.get("code", "")
            pattern = {"HK": r"HK\.\d{5}", "A": r"(?:SH|SZ|BJ)\.\d{6}",
                       "US": r"US\.[A-Z0-9][A-Z0-9.$^/-]{0,28}"}[market]
            if not isinstance(code, str) or not re.fullmatch(pattern, code):
                raise ValueError(f"Invalid cached catalog code: {code}")
            if code in by_code:
                if record != by_code[code]:
                    raise ValueError(f"Conflicting duplicate code: {code}")
                continue
            by_code[code] = record
        count, previous = len(by_code), self.previous_count(source)
        if previous and count < previous * 0.8:
            raise ValueError(f"Source shrank by >20%: {previous} -> {count}; existing data retained")
        with self.db.conn.cursor() as cursor:
            # Include older display names when upgrading an existing database.
            cursor.execute("""SELECT code,MAX(NULLIF(name_zh,'')),MAX(NULLIF(name_en,'')),
                              MAX(CASE WHEN name REGEXP '[一-龥]' THEN name END),MAX(NULLIF(name,''))
                              FROM stock_pools WHERE market=%s GROUP BY code""", (market,))
            old = {r[0]: {"name_zh": r[1], "name_en": r[2], "name": r[3] or r[4]} for r in cursor.fetchall()}
            cursor.execute("""SELECT code,name,name_zh,name_en,instrument_type,exchange
                              FROM stock_pools WHERE market=%s AND pool_type='catalog'""", (market,))
            old_catalog = {r[0]: r[1:] for r in cursor.fetchall()}
            cursor.execute("DROP TEMPORARY TABLE IF EXISTS tmp_stock_catalog_sync")
            cursor.execute("""CREATE TEMPORARY TABLE tmp_stock_catalog_sync (
                market VARCHAR(8) NOT NULL,code VARCHAR(32) NOT NULL,name VARCHAR(255) NOT NULL,
                name_zh VARCHAR(255) NULL,name_en VARCHAR(255) NULL,instrument_type VARCHAR(16) NOT NULL,
                exchange VARCHAR(32) NULL,extra_data JSON,PRIMARY KEY(market,code)
                ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            rows, names, changed = [], Counter(), 0
            for code, record in by_code.items():
                chosen = choose_names(market, code, record.get("name_zh"), record.get("name_en"), old.get(code))
                names["zh" if chosen["name_zh"] else "en" if chosen["name_en"] else "code"] += 1
                comparable = (chosen["name"], chosen["name_zh"], chosen["name_en"], record["instrument_type"], record.get("exchange"))
                changed += code in old_catalog and old_catalog[code] != comparable
                extra = json.dumps({"catalog_source": source, "original_name_zh": record.get("original_name_zh")}, ensure_ascii=False)
                rows.append((market, code, chosen["name"], chosen["name_zh"], chosen["name_en"], record["instrument_type"], record.get("exchange"), extra))
            sql = "INSERT INTO tmp_stock_catalog_sync VALUES (%s,%s,%s,%s,%s,%s,%s,%s)"
            for offset in range(0, len(rows), 500):
                cursor.executemany(sql, rows[offset:offset+500])
        now = datetime.now(ZONE).replace(tzinfo=None)
        try:
            self.db.conn.begin()
            with self.db.conn.cursor() as cursor:
                cursor.execute("""INSERT INTO stock_pools
                    (market,pool_type,code,name,name_zh,name_en,instrument_type,exchange,source,catalog_seen_at,extra_data)
                    SELECT market,'catalog',code,name,name_zh,name_en,instrument_type,exchange,'daily_catalog',%s,extra_data
                    FROM tmp_stock_catalog_sync WHERE 1=1
                    ON DUPLICATE KEY UPDATE name=VALUES(name),name_zh=VALUES(name_zh),name_en=VALUES(name_en),
                    instrument_type=VALUES(instrument_type),exchange=VALUES(exchange),
                    catalog_seen_at=VALUES(catalog_seen_at),
                    extra_data=JSON_MERGE_PATCH(COALESCE(stock_pools.extra_data,JSON_OBJECT()),VALUES(extra_data))""", (now,))
                cursor.execute("""UPDATE stock_pools p JOIN tmp_stock_catalog_sync s
                    ON p.market=s.market AND p.code=s.code SET p.name=s.name,p.name_zh=s.name_zh,
                    p.name_en=s.name_en,p.instrument_type=s.instrument_type
                    WHERE p.pool_type<>'catalog' AND
                    (NOT(p.name<=>s.name) OR NOT(p.name_zh<=>s.name_zh) OR NOT(p.name_en<=>s.name_en)
                     OR NOT(p.instrument_type<=>s.instrument_type))""")
            self.db.conn.commit()
        except Exception:
            self.db.conn.rollback()
            raise
        return {"seen": count, "inserted": len(set(by_code)-set(old_catalog)), "updated": changed, **names}

    def finish(self, error=None):
        states = [item.get("status") for item in self.sources.values()]
        status = "success" if states and all(s == "success" for s in states) else "partial" if any(s in {"success", "partial"} for s in states) else "failed"
        with self.db.conn.cursor() as cursor:
            cursor.execute("UPDATE stock_catalog_sync_runs SET status=%s,finished_at=%s,error_message=%s,sources_json=%s WHERE run_date=%s",
                           (status, datetime.now(ZONE).replace(tzinfo=None), error,
                            json.dumps(self.sources, ensure_ascii=False), self.day))
        return status
